"""Release-only adapter for Scene Graph Task Planner: stable paths and catalogue profiles.

This module does not exist in the research workspace. It replaces two workspace
assumptions of the copied code: that the legacy engines live under ``outputs/`` and
that the worker always reads the historical 33-skill library. The original core prompt
templates, validators and skill definitions are retained unchanged. Under the formal
profile the release adds one explicit, model-visible catalogue-availability block
(``release_catalogue_scope``: offered skills, withheld skills with reasons, and
``WITHHELD_RULE``) to the execution contract; the historical profile adds no such block.

Profiles
--------
``formal_v0_3_2_wire_compatible`` (default)
    The latest formal catalogue (0.3.2, 30 skills) is the published asset. The copied
    matrix ABI and retrieval metadata accept 12 of the 30 definitions; only those 12
    are offered to the planner. The other 18 are withheld and listed, with reasons,
    in ``catalogues/runtime_compatibility_v0_3_2.json`` and in every run output.
``historical33_replay``
    Historical catalogue compatibility: the 0.2.0 library (33 skills), byte-identical,
    with the original five-skill worker subset. The identifier is kept for continuity.
    It reproduces the historical catalogue and skill subset only; it makes no claim of
    exact, bit-identical end-to-end replay of any historical run (models, transport,
    prompts around the core and environment are not reproduced or verified).
"""

import hashlib
import json
import os
from pathlib import Path

RELEASE_VERSION = "scene_graph_task_planner_release_20261117"
RELEASE_ROOT = Path(__file__).resolve().parent.parent
ENGINE_ROOT = RELEASE_ROOT / "vendor" / "legacy_engines"
CATALOGUE_DIR = RELEASE_ROOT / "catalogues"
COMPATIBILITY_REPORT = CATALOGUE_DIR / "runtime_compatibility_v0_3_2.json"

PROFILE_ENV = "SGTP_CATALOGUE_PROFILE"
FORMAL_PROFILE = "formal_v0_3_2_wire_compatible"
HISTORICAL_PROFILE = "historical33_replay"
DEFAULT_PROFILE = FORMAL_PROFILE

HISTORICAL_NOTE = (
    "Existing five symbolic skill specifications, unchanged defaults; "
    "no real-robot capability claim.")
FORMAL_NOTE = (
    "Wire-compatible subset of skill catalogue 0.3.2; unchanged definitions and defaults; "
    "no real-robot capability claim.")

PROFILES = {
    FORMAL_PROFILE: {
        "description": "Formal catalogue 0.3.2; 12 of 30 skills offered (wire-compatible).",
        "catalogue": "skill_catalogue_v0_3_2.release.json",
        "catalogue_version": "0.3.2",
        "sha256": "49d28f713d4e49fbde236c34cfdb0b4a87ced260631e116d0977a7b6b163b75c",
        "skill_ids": ("navigate_to_object", "navigate_to_location", "grasp_object",
                      "lift_held_object", "place_object_on_surface",
                      "place_object_in_container", "release_object",
                      "open_articulated_object", "close_articulated_object", "press_control",
                      "handover_object", "bend_and_lift_object"),
        "integration_note": FORMAL_NOTE,
        "announce_withheld_skills": True,
    },
    HISTORICAL_PROFILE: {
        "description": ("Historical catalogue compatibility (0.2.0, five-skill subset); "
                        "no exact end-to-end replay claim."),
        "catalogue": "historical/quadruped_atomic_skill_library_v0_2_0.historical33.json",
        "catalogue_version": "0.2.0",
        "sha256": "63ca20375a1c33c0dd2d366ea66a92921c3c69a1ea8839b10f23a1fd055f5682",
        "skill_ids": ("navigate_to_object", "position_arm_for_object", "grasp_object",
                      "lift_held_object", "place_object_on_surface"),
        "integration_note": HISTORICAL_NOTE,
        "announce_withheld_skills": False,
    },
}

WITHHELD_RULE = (
    "Only offered_skill_ids are available to this planner. If completing the instruction "
    "requires a catalogue skill listed in withheld_catalogue_skills, do not substitute an "
    "offered skill for it; leave that action unplanned so the caller records an incomplete "
    "result.")


class CatalogueProfileError(ValueError):
    pass


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def profile_name(name=None):
    if name is not None:
        value = name
    else:
        value = os.environ.get(PROFILE_ENV, "").strip() or DEFAULT_PROFILE
    if value not in PROFILES:
        raise CatalogueProfileError("Unknown catalogue profile: " + str(value)
                                    + "; known: " + ", ".join(sorted(PROFILES)))
    return value


def active_profile(name=None):
    """Resolve and integrity-check one named catalogue; never fall back to another one."""
    name = profile_name(name)
    spec = PROFILES[name]
    path = CATALOGUE_DIR / spec["catalogue"]
    if not path.is_file():
        raise CatalogueProfileError("Catalogue asset missing: " + spec["catalogue"])
    digest = _sha256(path)
    if digest != spec["sha256"]:
        raise CatalogueProfileError("Catalogue asset changed: " + spec["catalogue"])
    library = json.loads(path.read_text(encoding="utf-8"))
    if library.get("version") != spec["catalogue_version"]:
        raise CatalogueProfileError("Catalogue version differs from profile: " + name)
    present = {s["skill_id"] for s in library["skills"]}
    missing = [s for s in spec["skill_ids"] if s not in present]
    if missing:
        raise CatalogueProfileError("Profile skills absent from catalogue: " + ", ".join(missing))
    return {"name": name, "library_path": path, "library_sha256": digest,
            "catalogue_version": spec["catalogue_version"],
            "catalogue_skill_count": len(library["skills"]),
            "skill_ids": tuple(spec["skill_ids"]),
            "description": spec["description"],
            "integration_note": spec["integration_note"],
            "announce_withheld_skills": spec["announce_withheld_skills"]}


def catalogue_scope(profile):
    """Public, model-visible statement of offered vs withheld catalogue skills."""
    if not profile["announce_withheld_skills"]:
        return None
    report = json.loads(COMPATIBILITY_REPORT.read_text(encoding="utf-8"))
    if report["catalogue"]["version"] != profile["catalogue_version"]:
        raise CatalogueProfileError("Compatibility report is for another catalogue version")
    offered = [r["skill_id"] for r in report["skills"] if r["offered_at_runtime"]]
    if tuple(offered) != profile["skill_ids"]:
        raise CatalogueProfileError("Compatibility report and runtime profile disagree")
    return {"catalogue_version": profile["catalogue_version"],
            "catalogue_skill_count": len(report["skills"]),
            "offered_skill_ids": offered,
            "withheld_catalogue_skills": [
                {"skill_id": r["skill_id"], "reasons": list(r["exclusion_reasons"])}
                for r in report["skills"] if not r["offered_at_runtime"]],
            "rule": WITHHELD_RULE}
