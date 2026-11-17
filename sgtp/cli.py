"""Command line for Scene Graph Task Planner. Planning only: nothing is executed.

Subcommands
-----------
``plan``          full-graph baseline: run the copied three-stage worker on a scene.
``topology``      pass-through to the copied topology filter CLI (prepare,
                  ingest-binding, route, slice, handoff, check-binding).
``plan-handoff``  run the same worker on a topology run's cropped scene and task.
``toy-scene``     write the owned synthetic toy inputs into a new directory.
``record``        turn a backend log into a recorded-response file for replay.
``verify-lock``   compare the release tree with release_source_lock.json.

Exit codes: 0 complete plan (or command succeeded), 2 usage or input error,
3 controlled stop / incomplete plan (an engineering JSON is still written).
"""

import argparse
import collections
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from . import RELEASE_VERSION
from . import lock as lock_module

RELEASE_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_DIR = RELEASE_ROOT / "vendor" / "legacy_engines" / "release_adapters"
SYNTHETIC_SPEC = "scripted:sgtp.synthetic_backend:respond"
BACKEND_KINDS = ("offline", "recorded", "scripted", "live")
PLACEHOLDER_KEY_ENV = "SGTP_NON_LIVE_PLACEHOLDER_KEY"
PLACEHOLDER_KEY_VALUE = "non-live-placeholder-not-a-credential"
NON_LIVE_MODEL = "no-model-non-live-backend"
NON_LIVE_BASE_URL = "https://non-live.invalid/v1"
VOCABULARY_ENV = "SGTP_SYNTHETIC_VOCABULARY"
EXIT_OK, EXIT_USAGE, EXIT_INCOMPLETE = 0, 2, 3
RUN_RECORD = "release_run.json"
GAP_RECORD = "catalogue_runtime_gap.json"
STATEMENT = ("Planning only. No plan was executed, no robot or simulator was contacted, and "
             "a complete engineering plan is a symbolic artifact, not an execution permission.")


class UsageError(ValueError):
    """Refused before any worker directory or model request was created."""


def backend_module():
    """The same module object the vendored transports import (flat name)."""
    if str(ADAPTER_DIR) not in sys.path:
        sys.path.insert(0, str(ADAPTER_DIR))
    import release_backend
    return release_backend


def release_module():
    from embodied_pipeline import _release
    return _release


def backend_spec(value):
    return SYNTHETIC_SPEC if value == "synthetic" else value


def _write_new(path, value):
    path = Path(path)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def configure(args, out_dir, scene_dir):
    """Validate the backend choice and set the environment the worker and its
    alignment subprocess inherit. Raises UsageError before anything is created."""
    backend = backend_module()
    release = release_module()
    spec = backend_spec(args.backend)
    kind, _, argument = spec.partition(":")
    if kind not in BACKEND_KINDS:
        raise UsageError("Unknown backend " + repr(args.backend) + "; use offline, synthetic, "
                         "recorded:<file>, scripted:<module>:<callable> or live")
    if kind in ("recorded", "scripted") and not argument:
        raise UsageError(kind + " backend needs an argument")
    profile = release.active_profile(args.catalogue_profile)
    live = kind == "live"
    if live:
        if not args.acknowledge_live_api:
            raise UsageError("--backend live requires --acknowledge-live-api; no request made")
        missing = [flag for flag, value in (("--model", args.model),
                                            ("--base-url", args.base_url),
                                            ("--api-key-env", args.api_key_env)) if not value]
        if missing:
            raise UsageError("--backend live requires " + ", ".join(missing))
        if not os.environ.get(args.api_key_env, "").strip():
            raise UsageError("Environment variable " + args.api_key_env
                             + " is empty; no request made")
    elif args.acknowledge_live_api:
        raise UsageError("--acknowledge-live-api is only meaningful with --backend live")
    plan_scope = (args.plan_scope or "").strip() or None
    if kind == "recorded":
        recorded = _read(argument)
        # Early, user-facing checks; release_backend re-checks every response against the
        # exact bytes (request_sha256) of the request actually being sent.
        if not isinstance(recorded, dict) or recorded.get("format") != backend.RECORDED_FORMAT:
            raise UsageError("Recorded file has an unknown or older format without request "
                             "identities (expected " + backend.RECORDED_FORMAT + "); record "
                             "the run again")
        if recorded.get("catalogue_profile") != profile["name"]:
            raise UsageError("Recorded file was made under catalogue profile "
                             + repr(recorded.get("catalogue_profile")) + ", not "
                             + repr(profile["name"]))
        recorded_scope = recorded.get("plan_scope")
        if plan_scope is not None and plan_scope != recorded_scope:
            raise UsageError("--plan-scope differs from the recorded file's plan_scope")
        plan_scope = recorded_scope
    environment = {
        backend.BACKEND_ENV: spec,
        backend.LOG_ENV: str(Path(out_dir).resolve() / "backend_log.jsonl"),
        backend.ACK_ENV: "1" if live else "",
        backend.SCOPE_ENV: plan_scope or "",
        release.PROFILE_ENV: profile["name"],
        VOCABULARY_ENV: str(Path(scene_dir).resolve() / "scene_graph.json")
        if kind == "scripted" else "",
    }
    if not live:
        environment[PLACEHOLDER_KEY_ENV] = PLACEHOLDER_KEY_VALUE
    os.environ.update(environment)
    worker_args = SimpleNamespace(
        model=args.model if live else (args.model or NON_LIVE_MODEL),
        base_url=args.base_url if live else NON_LIVE_BASE_URL,
        api_key_env=args.api_key_env if live else PLACEHOLDER_KEY_ENV,
        request_timeout=args.request_timeout,
        semantic_audit_mode=args.semantic_audit_mode)
    return {"spec": spec, "kind": kind, "live": live, "plan_scope": plan_scope,
            "profile": profile, "worker_args": worker_args}


def load_worker(profile_name):
    """Import the worker after the profile is set; refuse a different profile in-process."""
    from embodied_pipeline.orchestration import task_collection_worker as worker
    loaded = worker.CATALOGUE_PROFILE["name"]
    if loaded != profile_name:
        raise UsageError("The worker was already imported with catalogue profile "
                         + repr(loaded) + "; start a new process for " + repr(profile_name))
    return worker


def backend_decisions(log_path):
    entries = []
    if Path(log_path).is_file():
        entries = [json.loads(line) for line in
                   Path(log_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    counts = collections.Counter((e.get("stage"), e.get("decision")) for e in entries)
    invoked = any(e.get("model_invoked") == "attempted" for e in entries)
    return entries, [{"stage": stage, "decision": decision, "count": count}
                     for (stage, decision), count in sorted(counts.items(), key=str)], invoked


def run_plan(args, task, scene_dir, out_dir, command):
    out_dir = Path(out_dir)
    scene_dir = Path(scene_dir)
    if not (scene_dir / "scene_graph.json").is_file():
        raise UsageError("Scene directory has no scene_graph.json: " + str(scene_dir))
    config = configure(args, out_dir, scene_dir)
    worker = load_worker(config["profile"]["name"])
    out_dir.mkdir(parents=True, exist_ok=False)
    release = release_module()
    scope = release.catalogue_scope(config["profile"])
    if scope is not None:
        _write_new(out_dir / GAP_RECORD, dict(scope, release_version=RELEASE_VERSION,
                                             note="Withheld skills are catalogue definitions "
                                                  "the copied matrix ABI cannot offer; see "
                                                  "catalogues/runtime_compatibility_v0_3_2.json."))
    error = None
    summary = {}
    try:
        summary = worker.run_task(config["worker_args"], task, scene_dir, out_dir / "worker", None)
    except Exception as exc:  # recorded below; the worker already wrote its own envelope
        error = {"error_type": type(exc).__name__, "error": str(exc)}
    entries, decisions, invoked = backend_decisions(out_dir / "backend_log.jsonl")
    engineering_path = out_dir / "worker" / "engineering_plan.json"
    engineering = _read(engineering_path) if engineering_path.is_file() else None
    completeness = (engineering or {}).get("artifact_completeness")
    profile = config["profile"]
    record = {
        "release_version": RELEASE_VERSION,
        "command": command,
        "statement": STATEMENT,
        "execution_performed": False,
        "robot_or_simulator_contacted": False,
        "backend": {"kind": config["kind"], "spec": config["spec"],
                    "synthetic_responder": config["spec"] == SYNTHETIC_SPEC,
                    "live_api_acknowledged": config["live"],
                    "plan_scope": config["plan_scope"],
                    "decisions": decisions},
        "model_invoked": "attempted" if invoked else False,
        "catalogue_profile": {
            "name": profile["name"], "description": profile["description"],
            "catalogue_version": profile["catalogue_version"],
            "catalogue_skill_count": profile["catalogue_skill_count"],
            "library_sha256": profile["library_sha256"],
            "offered_skill_ids": list(profile["skill_ids"]),
            "withheld_skill_count": None if scope is None
            else len(scope["withheld_catalogue_skills"]),
            "runtime_gap_file": GAP_RECORD if scope is not None else None},
        "task": {key: task.get(key) for key in ("record_id", "scene_id", "instruction",
                                                "input_scope")},
        "scene_dir": str(scene_dir.resolve()),
        "worker_dir": "worker",
        "engineering_plan": "worker/engineering_plan.json" if engineering else None,
        "artifact_completeness": completeness,
        "plan_is_null": None if engineering is None else engineering.get("plan") is None,
        "planning_completed": summary.get("planning_completed"),
        "usable_as_plan": summary.get("usable_as_plan"),
        "worker_status": summary.get("status"),
        "failed_stage": summary.get("failed_stage"),
        "worker_error": summary.get("error"),
        "wrapper_error": error,
        "backend_responses_served_without_model": sum(
            1 for e in entries if e.get("decision") == "served_without_model"),
    }
    _write_new(out_dir / RUN_RECORD, record)
    print(json.dumps({"out_dir": str(out_dir), "artifact_completeness": completeness,
                      "model_invoked": record["model_invoked"],
                      "backend": config["spec"]}, ensure_ascii=False))
    if error is not None:
        return EXIT_USAGE
    return EXIT_OK if completeness == "complete" else EXIT_INCOMPLETE


def scene_id_of(scene_dir, explicit):
    if explicit:
        return explicit
    path = Path(scene_dir) / "scene_metadata.json"
    if path.is_file():
        value = _read(path).get("scene_id")
        if isinstance(value, str) and value.strip():
            return value
    return "scene"


def cmd_plan(args):
    task = {"record_id": args.record_id, "scene_id": scene_id_of(args.scene_dir, args.scene_id),
            "instruction": args.instruction}
    return run_plan(args, task, args.scene_dir, args.out_dir, "plan")


def cmd_plan_handoff(args):
    from embodied_pipeline.topology_filter import stage as stage_module
    run_dir = Path(args.run_dir)
    try:
        stage_module.require_stage(run_dir, "handoff")
    except ValueError as exc:
        raise UsageError(str(exc)) from exc
    task = _read(run_dir / "handoff" / "task.json")
    return run_plan(args, task, run_dir / "slice" / "scene", args.out_dir, "plan-handoff")


def cmd_topology(args):
    from embodied_pipeline.topology_filter import cli as topology_cli
    rest = list(args.topology_args)
    if rest and rest[0] == "--":
        rest = rest[1:]
    return topology_cli.main(rest)


def cmd_toy_scene(args):
    from . import toy
    manifest = toy.write_toy(args.out_dir, args.variant)
    print(json.dumps(manifest, ensure_ascii=False))
    return EXIT_OK


def cmd_record(args):
    backend = backend_module()
    try:
        document = backend.recorded_from_log(args.backend_log)
    except backend.BackendRefused as exc:
        raise UsageError(str(exc)) from exc
    _write_new(args.out, document)
    print(json.dumps({"out": str(args.out), "plan_scope": document["plan_scope"],
                      "stages": {k: len(v) for k, v in document["responses"].items()}}))
    return EXIT_OK


def cmd_verify_lock(args):
    report = lock_module.verify(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return EXIT_OK if report["passed"] else EXIT_INCOMPLETE


def add_backend_options(parser):
    parser.add_argument("--out-dir", required=True, help="New directory (must not exist)")
    parser.add_argument("--backend", default="offline",
                        help="offline (default, no request), synthetic, recorded:<file>, "
                             "scripted:<module>:<callable>, or live")
    parser.add_argument("--acknowledge-live-api", action="store_true",
                        help="Required with --backend live; may incur provider charges")
    parser.add_argument("--catalogue-profile", default=None,
                        help="formal_v0_3_2_wire_compatible (default) or historical33_replay")
    parser.add_argument("--plan-scope", default=None,
                        help="Fixed typed-identifier plan scope; needed to record a replayable "
                             "run. Default: a random scope (original behaviour)")
    parser.add_argument("--model", default=None, help="Model id (live backend only)")
    parser.add_argument("--base-url", default=None,
                        help="OpenAI-compatible base URL ending in /v1 (live backend only)")
    parser.add_argument("--api-key-env", default=None,
                        help="Name of the environment variable holding the key (live only)")
    parser.add_argument("--request-timeout", type=float, default=180.0)
    parser.add_argument("--semantic-audit-mode", default="annotate",
                        choices=("annotate", "block", "off"))


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m sgtp", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Full-graph three-stage planning baseline")
    plan.add_argument("--scene-dir", required=True)
    plan.add_argument("--instruction", required=True)
    plan.add_argument("--record-id", default="sgtp_0001")
    plan.add_argument("--scene-id", default=None,
                      help="Default: scene_metadata.json scene_id, else 'scene'")
    add_backend_options(plan)
    plan.set_defaults(handler=cmd_plan)

    handoff = sub.add_parser("plan-handoff",
                             help="Plan on a completed topology run's cropped scene")
    handoff.add_argument("--run-dir", required=True)
    add_backend_options(handoff)
    handoff.set_defaults(handler=cmd_plan_handoff)

    topology = sub.add_parser("topology", help="Topology filter (copied CLI, unchanged)")
    topology.add_argument("topology_args", nargs=argparse.REMAINDER)
    topology.set_defaults(handler=cmd_topology)

    toy = sub.add_parser("toy-scene", help="Write the synthetic toy inputs")
    toy.add_argument("--out-dir", required=True)
    toy.add_argument("--variant", default="base", choices=("base", "ambiguous", "closed_ab"))
    toy.set_defaults(handler=cmd_toy_scene)

    record = sub.add_parser("record", help="Backend log -> recorded responses file")
    record.add_argument("--backend-log", required=True)
    record.add_argument("--out", required=True)
    record.set_defaults(handler=cmd_record)

    verify = sub.add_parser("verify-lock", help="Check files against release_source_lock.json")
    verify.add_argument("--root", default=str(RELEASE_ROOT))
    verify.set_defaults(handler=cmd_verify_lock)
    return parser


def main(argv=None):
    if str(RELEASE_ROOT) not in sys.path:
        sys.path.insert(0, str(RELEASE_ROOT))
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (UsageError, FileExistsError, FileNotFoundError) as exc:
        print("sgtp: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return EXIT_USAGE
    except ValueError as exc:
        # Includes CatalogueProfileError and BackendRefused-free configuration errors.
        print("sgtp: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return EXIT_USAGE
