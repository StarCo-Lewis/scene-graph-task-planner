"""Owned, synthetic toy inputs for Scene Graph Task Planner (no real scene, no robot data).

Ported from the authors' offline topology fixture: four square rooms a-b-c-d with
portals a-b, b-c and b-d, two markers, one wall panel straddling a room boundary
(an ``unknown`` region assignment) and a few furniture items. The overlay is built
with the release's own ``overlay`` helpers, so its region index and coverage are
computed, not hand-written. Variants:

``base``       every target is unique.
``ambiguous``  a second ``blue marker`` (id 13) in room d.
``closed_ab``  portal a-b is declared closed, so room c is unreachable from room a.
"""

import copy
import hashlib
import json
from pathlib import Path

from embodied_pipeline.topology_filter import binding as binding_module
from embodied_pipeline.topology_filter import overlay as overlay_module

SCENE_ID = "sgtp_toy_scene"
FRAME = "synthetic_world_z_up_m"
START_REGION = "region_a"
INSTRUCTION = "Visit the red marker, then the blue marker, and finally return to the red marker."
VARIANTS = ("base", "ambiguous", "closed_ab")
REGION_RECTS = {
    "region_a": [0.0, 0.0, 2.0, 2.0],
    "region_b": [2.0, 0.0, 4.0, 2.0],
    "region_c": [4.0, 0.0, 6.0, 2.0],
    "region_d": [0.0, 4.0, 2.0, 6.0],
}
FLOORS = {"region_a": 1, "region_b": 2, "region_c": 3, "region_d": 4}


def node(node_id, tag, center_xy, extent_xy, extent_z=0.4):
    return {"id": node_id, "object_tag": tag, "caption": tag + " caption",
            "bbox_center": [center_xy[0], center_xy[1], 0.5],
            "bbox_extent": [extent_xy[0], extent_xy[1], extent_z],
            "active": True, "semantic_status": "recognized",
            "instance_identity_verified": False, "uncertainty_reason": None}


def nodes(variant="base"):
    _check(variant)
    result = [
        node(1, "floor a", (1.0, 1.0), (2.0, 2.0), 0.1),
        node(2, "floor b", (3.0, 1.0), (2.0, 2.0), 0.1),
        node(3, "floor c", (5.0, 1.0), (2.0, 2.0), 0.1),
        node(4, "floor d", (1.0, 5.0), (2.0, 2.0), 0.1),
        node(10, "red marker", (0.5, 0.5), (0.2, 0.2)),
        node(11, "blue marker", (5.5, 1.5), (0.2, 0.2)),
        node(12, "wall panel", (2.0, 1.0), (0.4, 2.0)),
        node(20, "shelf in d", (0.5, 4.5), (0.4, 0.4)),
        node(21, "crate in c", (4.5, 0.5), (0.4, 0.4)),
        node(22, "rack in d", (1.5, 5.5), (0.4, 0.4)),
        node(23, "lonely box in d", (1.0, 4.2), (0.3, 0.3)),
    ]
    if variant == "ambiguous":
        result.append(node(13, "blue marker", (1.5, 4.5), (0.2, 0.2)))
    return result


def edges():
    # Unverified support hypotheses only; never passages.
    return [
        {"source": 21, "target": 20, "relation": "a on b", "verified": False,
         "physics_verified": False},
        {"source": 12, "target": 22, "relation": "a on b", "verified": False,
         "physics_verified": False},
    ]


def metadata():
    return {"scene_id": SCENE_ID, "coordinate_frame": FRAME, "length_unit": "m",
            "derived_metadata": True, "source_metadata_available": False,
            "synthetic_toy_scene": True}


def connections(variant="base"):
    _check(variant)
    ab = "closed" if variant == "closed_ab" else "unknown"
    return [("portal_ab", ["region_a", "region_b"], ab),
            ("portal_bc", ["region_b", "region_c"], "unknown"),
            ("portal_bd", ["region_b", "region_d"], "unknown")]


def overlay(node_list, graph_sha, edges_sha, variant="base"):
    rects = {region_id: tuple(rect) for region_id, rect in REGION_RECTS.items()}
    index = []
    for item in node_list:
        result = overlay_module.classify_node(overlay_module.node_xy_bounds(item), rects, 1e-5)
        index.append({"object_id": item["id"], "region_id": result["region_id"],
                      "status": result["status"],
                      "assignment_basis": "source_frame_geometry"
                      if result["status"] == "assigned" else "unknown",
                      "containment": result["containment"],
                      "overlapping_region_ids": result["overlapping_region_ids"],
                      "source_agreement": "not_covered_by_topology_candidate"})
    return {
        "overlay_version": overlay_module.OVERLAY_VERSION,
        "scene_id": SCENE_ID,
        "coordinate_frame": FRAME,
        "length_unit": "m",
        "provenance": {
            "geometry_source": "synthetic_offline_fixture",
            "geometry_source_files": [{"path": "synthetic", "sha256": None, "bytes": None,
                                       "sha256_recomputed": False}],
            "frame_composition": "pure_translation_no_rotation",
            "scene_graph_sha256": graph_sha,
            "scene_graph_edges_sha256": edges_sha,
            "prepared_by": "sgtp.toy (synthetic release example)",
            "prepared_at": "2026-11-17T00:00:00Z",
            "is_ground_truth": False, "human_verified": False,
            "visual_verification_performed": False, "robot_map_derived": False,
            "structural_connectivity_only": True, "physical_reachability_claimed": False},
        "assignment_policy": {
            "method": "full_bbox_xy_strict_containment", "numeric_tolerance_m": 1e-05,
            "rule": "Whole XY bbox contained by exactly one rectangle.",
            "unknown_retention": "global_retain",
            "unknown_retention_statement": "Unknown objects remain in every crop."},
        "regions": [{
            "region_id": region_id, "region_type": "room",
            "source_room_name": region_id.replace("region_", ""),
            "visual_label_from_graph": None, "author_label_legacy": None,
            "label_conflict": False, "floor_object_id": FLOORS[region_id],
            "frame_translation_xy": [(rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2],
            "floor_rect_xy": list(rect), "rect_basis": "synthetic_rect",
            "frame_floor_match": {"status": "within_tolerance", "residual_m": 0.0,
                                  "tolerance_m": 0.01},
            "floor_polygon_xy": None, "area_m2": None}
            for region_id, rect in REGION_RECTS.items()],
        "connections": [{
            "portal_id": portal_id, "regions": regions, "portal_type": "doorway",
            "door_state": door_state, "evidence": "synthetic_fixture",
            "evidence_detail": None, "derived_from_object_proximity": False,
            "geometry_verified": False, "traversability": "unknown",
            "width_m": None, "length_m": None}
            for portal_id, regions, door_state in connections(variant)],
        "object_region_index": index,
        "coverage": overlay_module.coverage_counters(index),
        "unknowns": [{"topic": "Synthetic toy scene", "detail": "Release example data only."}],
        "routing_policy": {"search": "bfs_min_edge_count", "metric_distance_available": False,
                           "statement": "Hop count only."},
    }


def prebinding_bound(instruction=INSTRUCTION):
    """A hand-authored, schema-valid prebinding for the toy task (not model output)."""
    return {
        "binding_version": binding_module.BINDING_VERSION,
        "task_class": binding_module.SUPPORTED_TASK_CLASS,
        "scene_id": SCENE_ID,
        "instruction": instruction,
        "instruction_sha256": binding_module.instruction_sha256(instruction),
        "role_order_is_not_action_order": True,
        "roles": [
            {"role_id": "role_001", "role_kind": "visit_target",
             "role_text": "the red marker", "expected_labels": ["red marker"],
             "status": "bound", "object_id": 10, "candidate_object_ids": [],
             "region_id": "region_a", "clarification_question": None,
             "evidence": "object_tag"},
            {"role_id": "role_002", "role_kind": "visit_target",
             "role_text": "the blue marker", "expected_labels": ["blue marker"],
             "status": "bound", "object_id": 11, "candidate_object_ids": [],
             "region_id": "region_c", "clarification_question": None,
             "evidence": "object_tag"},
        ],
        "visit_role_ids": ["role_001", "role_002", "role_001"],
        "terminal_role_id": "role_001",
        "terminal_robot_location_required": True,
        "stop_execution": False,
        "needs_environment_context": False,
        "clarification_question": None,
        "missing_information": [],
    }


def prebinding_ambiguous(instruction=INSTRUCTION):
    """The same task when two blue markers exist: stop and ask, never pick one."""
    document = prebinding_bound(instruction)
    document["roles"][1].update(status="ambiguous", object_id=None, region_id=None,
                                candidate_object_ids=[11, 13],
                                clarification_question=(
                                    "There are two blue markers, one in room c and one in "
                                    "room d. Which one should be visited?"))
    document.update(visit_role_ids=[], terminal_role_id=None, stop_execution=True,
                    terminal_robot_location_required=False,
                    stop_reason="clarification_required")
    return document


def _check(variant):
    if variant not in VARIANTS:
        raise ValueError("Unknown toy variant: " + repr(variant) + "; known: "
                         + ", ".join(VARIANTS))


def _dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _write_new(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_toy(out_dir, variant="base"):
    """Write scene/, overlay.json and prebinding examples into a NEW directory."""
    _check(variant)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)
    scene = out_dir / "scene"
    node_list = nodes(variant)
    graph_sha = _write_new(scene / "scene_graph.json", _dump(node_list))
    edges_sha = _write_new(scene / "scene_graph_edges.json", _dump(edges()))
    _write_new(scene / "scene_metadata.json", _dump(metadata()))
    _write_new(out_dir / "overlay.json",
               _dump(overlay(node_list, graph_sha, edges_sha, variant)))
    _write_new(out_dir / "prebinding_bound.json", _dump(prebinding_bound()))
    if variant == "ambiguous":
        _write_new(out_dir / "prebinding_ambiguous.json", _dump(prebinding_ambiguous()))
    manifest = {"variant": variant, "scene_id": SCENE_ID, "instruction": INSTRUCTION,
                "start_region_id": START_REGION, "synthetic": True,
                "files": sorted(str(p.relative_to(out_dir)).replace("\\", "/")
                                for p in out_dir.rglob("*") if p.is_file())}
    _write_new(out_dir / "toy_manifest.json", _dump(copy.deepcopy(manifest)))
    return manifest
