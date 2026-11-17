"""Exclusive stage directories and the mandatory incomplete plan envelope.

Every stage that can be terminated in a catchable way still delivers an
``engineering_plan.json`` with ``plan: null``, so a consumer can tell "delivered"
from "planned" without inspecting prose. Stage directories are created
exclusively and existing evidence is never overwritten.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from . import PIPELINE_VERSION
from ..contracts import evidence_log

ARTIFACT_VERSION = "topology_filter_stage_artifact_v1"
INCOMPLETE = "incomplete"
STAGES = ("prepare", "ingest_binding", "route", "slice", "handoff", "check_binding")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    """Create a JSON file exactly once; an existing file is never replaced."""
    path = Path(path)
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    stream = path.open("x", encoding="utf-8")
    try:
        with stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def copy_new(source, destination):
    """Byte-exact copy that refuses to overwrite, with a digest of what was written."""
    data = Path(source).read_bytes()
    stream = Path(destination).open("xb")
    try:
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            Path(destination).unlink()
        except OSError:
            pass
        raise
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_stage(run_dir, stage, fresh_run=False):
    """Exclusively create ``<run_dir>/<stage>``; a repeated stage needs a new run dir.

    ``fresh_run`` additionally requires the run directory itself not to exist, so the
    first stage cannot be bolted onto a directory that already holds other evidence.
    """
    if stage not in STAGES:
        raise ValueError("Unknown topology filter stage: " + str(stage))
    run_dir = Path(run_dir)
    if fresh_run:
        run_dir.mkdir(parents=True, exist_ok=False)
    elif not run_dir.is_dir():
        raise ValueError("Run directory does not exist: " + str(run_dir))
    stage_dir = run_dir / stage
    stage_dir.mkdir(exist_ok=False)
    write_new(stage_dir / "stage_started.json", {
        "pipeline_version": PIPELINE_VERSION, "stage": stage, "started_at_utc": now_iso(),
        "automatic_replay_permitted": False, "model_requests_made_by_this_stage": 0})
    return stage_dir


def stage_record_id(stage_dir):
    """The record id this run was prepared for, or ``None`` when it cannot be read."""
    try:
        config = read_json(Path(stage_dir).parent / "prepare" / "config.json")
    except (OSError, ValueError):
        return None
    return config.get("record_id") if isinstance(config, dict) else None


def deliver_failure(stage_dir, stage, reason, detail=None, extra=None, record_id=None):
    """Write the incomplete envelope plus a stage failure record. Never overwrites.

    The engineering JSON carries no model evidence prose: that moves to an exclusive
    audit log next to it. ``stage_failure.json`` keeps the original record unchanged.
    The returned envelope is the one written to disk.
    """
    stage_dir = Path(stage_dir)
    envelope = {
        "artifact_version": ARTIFACT_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "stage": stage,
        "plan": None,
        "artifact_completeness": INCOMPLETE,
        "planning_completed": False,
        "engineering_plan_file_written": True,
        "engineering_json_generated": False,
        "usable_as_plan": False,
        "execution_ready": False,
        "usable": False,
        "robot_executed": False,
        "model_requests_made": 0,
        "failed_stage": stage,
        "reason": reason,
        "detail": detail,
        "failure_is_correctness_grade": False,
        "delivered_at_utc": now_iso(),
    }
    if extra:
        envelope["evidence"] = extra
    if record_id is None:
        record_id = stage_record_id(stage_dir)
    view = evidence_log.engineering_view(
        envelope, stage_dir, stage, attempt=stage_dir.parent.parent.name,
        record_id=record_id)
    write_new(stage_dir / "engineering_plan.json", view)
    write_new(stage_dir / "stage_failure.json", {
        "pipeline_version": PIPELINE_VERSION, "stage": stage, "reason": reason,
        "detail": detail, "evidence": extra, "recorded_at_utc": now_iso(),
        "retry_policy": "No automatic retry; a new attempt requires a new run directory."})
    return view


def deliver_stage_result(stage_dir, stage, payload):
    """Successful stage completion marker; still not a plan and still not executable."""
    write_new(Path(stage_dir) / "stage_result.json", {
        "pipeline_version": PIPELINE_VERSION, "stage": stage, "completed_at_utc": now_iso(),
        "plan": None, "artifact_completeness": INCOMPLETE, "execution_ready": False,
        "planning_completed": False, "usable_as_plan": False, "usable": False,
        "model_requests_made": 0, "status": "stage_completed", "result": payload})


def record_artifacts(stage_dir, stage, relative_paths):
    """Hash every artifact this stage produced so a successor can detect a later edit."""
    stage_dir = Path(stage_dir)
    manifest = {"pipeline_version": PIPELINE_VERSION, "stage": stage,
                "recorded_at_utc": now_iso(),
                "artifacts": {name: sha256_file(stage_dir / name)
                              for name in sorted(relative_paths)}}
    write_new(stage_dir / "stage_artifacts.json", manifest)
    return manifest


def require_stage(run_dir, stage):
    """Refuse to continue unless the predecessor stage completed and is unmodified.

    A manual invocation cannot bypass this: a stopped stage leaves stage_failure.json
    and no stage_result.json, and any later edit breaks the recorded artifact digest.
    """
    stage_dir = Path(run_dir) / stage
    if not stage_dir.is_dir():
        raise ValueError("Required predecessor stage has not run: " + stage)
    if (stage_dir / "stage_failure.json").is_file():
        raise ValueError("Predecessor stage stopped and must not be continued: " + stage)
    result_path = stage_dir / "stage_result.json"
    if not result_path.is_file():
        raise ValueError("Predecessor stage produced no success marker: " + stage)
    result = read_json(result_path)
    if result.get("status") != "stage_completed":
        raise ValueError("Predecessor stage marker is not a completion: " + stage)
    manifest_path = stage_dir / "stage_artifacts.json"
    if not manifest_path.is_file():
        raise ValueError("Predecessor stage recorded no artifact manifest: " + stage)
    manifest = read_json(manifest_path)
    for name, digest in sorted(manifest["artifacts"].items()):
        actual = sha256_file(stage_dir / name)
        if actual != digest:
            raise ValueError("Artifact changed after stage " + stage + ": " + name
                             + " recorded=" + digest + " actual=" + actual)
    return result
