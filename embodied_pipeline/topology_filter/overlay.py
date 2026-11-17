"""Load and independently re-verify a scene_topology_overlay_v1 file.

Offline only: reads local JSON, recomputes the geometric object-to-region
assignment from the scene graph and refuses an overlay whose declared index or
coverage counters disagree with the recomputation.

Membership uses the overlay's own numeric tolerance (shared floating-point rect
edges, order 1e-5 m). The much larger frame/floor residual tolerance recorded in
``frame_floor_match`` is a provenance check only and is never used for
membership.
"""

import hashlib
import json
import math
from pathlib import Path

OVERLAY_VERSION = "scene_topology_overlay_v1"
MAX_MEMBERSHIP_TOLERANCE_M = 0.001
REGION_TYPES = ("room", "corridor", "unknown")
PORTAL_TYPES = ("doorway", "opening", "unknown")
DOOR_STATES = ("unknown", "open", "closed")
ASSIGNMENT_BASES = ("source_frame_geometry", "topology_candidate", "unknown")
CONTAINMENTS = ("fully_inside", "cross_boundary", "outside_all_regions", "no_geometry")
AGREEMENTS = ("agrees_with_topology_candidate", "conflicts_with_topology_candidate",
              "not_covered_by_topology_candidate")
TOP_LEVEL = ("overlay_version", "scene_id", "coordinate_frame", "length_unit", "provenance",
             "assignment_policy", "regions", "connections", "object_region_index", "coverage",
             "unknowns", "routing_policy")
PROVENANCE_CONSTANTS = {"is_ground_truth": False, "visual_verification_performed": False,
                        "robot_map_derived": False, "structural_connectivity_only": True,
                        "physical_reachability_claimed": False}


class OverlayError(ValueError):
    """The overlay is unusable as a filter input; never silently repaired."""


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _rect(values):
    if not (isinstance(values, list) and len(values) == 4 and all(_number(v) for v in values)):
        return None
    min_x, min_y, max_x, max_y = (float(v) for v in values)
    if not (min_x < max_x and min_y < max_y):
        return None
    return (min_x, min_y, max_x, max_y)


def rect_contains(rect, bounds, tolerance):
    return (bounds[0] >= rect[0] - tolerance and bounds[1] >= rect[1] - tolerance
            and bounds[2] <= rect[2] + tolerance and bounds[3] <= rect[3] + tolerance)


def rect_overlaps(rect, bounds, tolerance):
    """Positive-area overlap only; a shared edge within tolerance is not an overlap."""
    return (bounds[0] < rect[2] - tolerance and bounds[2] > rect[0] + tolerance
            and bounds[1] < rect[3] - tolerance and bounds[3] > rect[1] + tolerance)


def node_xy_bounds(node):
    """Full XY footprint of a node, or None when the geometry is unusable.

    A negative extent is rejected rather than normalised: silently taking its
    absolute value would turn malformed geometry into a confident assignment.
    """
    center, extent = node.get("bbox_center"), node.get("bbox_extent")
    if not (isinstance(center, list) and isinstance(extent, list)
            and len(center) >= 2 and len(extent) >= 2):
        return None
    if not all(_number(v) for v in (center[0], center[1], extent[0], extent[1])):
        return None
    if float(extent[0]) < 0.0 or float(extent[1]) < 0.0:
        return None
    half_x, half_y = float(extent[0]) / 2.0, float(extent[1]) / 2.0
    return (float(center[0]) - half_x, float(center[1]) - half_y,
            float(center[0]) + half_x, float(center[1]) + half_y)


def classify_node(bounds, rects, tolerance):
    """Strict containment in exactly one rectangle, else an explicit unknown reason."""
    if bounds is None:
        return {"status": "unknown", "region_id": None, "containment": "no_geometry",
                "overlapping_region_ids": []}
    inside = sorted(rid for rid, rect in rects.items() if rect_contains(rect, bounds, tolerance))
    if len(inside) == 1:
        return {"status": "assigned", "region_id": inside[0], "containment": "fully_inside",
                "overlapping_region_ids": []}
    touching = sorted(rid for rid, rect in rects.items() if rect_overlaps(rect, bounds, tolerance))
    if len(inside) > 1:
        return {"status": "unknown", "region_id": None, "containment": "cross_boundary",
                "overlapping_region_ids": inside}
    if touching:
        return {"status": "unknown", "region_id": None, "containment": "cross_boundary",
                "overlapping_region_ids": touching}
    return {"status": "unknown", "region_id": None, "containment": "outside_all_regions",
            "overlapping_region_ids": []}


def region_rects(overlay):
    rects = {}
    for region in overlay["regions"]:
        rect = _rect(region.get("floor_rect_xy"))
        if rect is None:
            raise OverlayError("Invalid floor_rect_xy for " + str(region.get("region_id")))
        rects[region["region_id"]] = rect
    return rects


def recompute_object_regions(nodes, overlay):
    """Independent recomputation keyed by node id; the overlay is never trusted blindly."""
    tolerance = float(overlay["assignment_policy"]["numeric_tolerance_m"])
    rects = region_rects(overlay)
    result = {}
    for node in nodes:
        result[node["id"]] = classify_node(node_xy_bounds(node), rects, tolerance)
    return result


def coverage_counters(index):
    counters = {"total_objects": len(index), "assigned": 0, "unknown": 0,
                "cross_boundary": 0, "outside_all_regions": 0, "no_geometry": 0}
    for entry in index:
        counters["assigned" if entry["status"] == "assigned" else "unknown"] += 1
        if entry["containment"] in ("cross_boundary", "outside_all_regions", "no_geometry"):
            counters[entry["containment"]] += 1
    return counters


def _check_structure(overlay, errors):
    if overlay.get("overlay_version") != OVERLAY_VERSION:
        errors.append("overlay_version must be " + OVERLAY_VERSION)
    for key in TOP_LEVEL:
        if key not in overlay:
            errors.append("Missing overlay key: " + key)
    if overlay.get("length_unit") != "m":
        errors.append("length_unit must be 'm'")
    provenance = overlay.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance must be an object")
    else:
        for key, expected in PROVENANCE_CONSTANTS.items():
            if provenance.get(key) is not expected:
                errors.append("provenance." + key + " must be " + repr(expected))
        for key in ("scene_graph_sha256", "scene_graph_edges_sha256", "prepared_by", "prepared_at",
                    "geometry_source", "frame_composition"):
            if not (isinstance(provenance.get(key), str) and provenance[key].strip()):
                errors.append("provenance." + key + " must be a nonempty string")
        files = provenance.get("geometry_source_files")
        if not (isinstance(files, list) and files):
            errors.append("provenance.geometry_source_files must be a nonempty array")
    policy = overlay.get("assignment_policy")
    if not isinstance(policy, dict):
        errors.append("assignment_policy must be an object")
    else:
        if policy.get("method") != "full_bbox_xy_strict_containment":
            errors.append("assignment_policy.method is not the supported method")
        if policy.get("unknown_retention") != "global_retain":
            errors.append("assignment_policy.unknown_retention must be global_retain")
        tolerance = policy.get("numeric_tolerance_m")
        if not (_number(tolerance) and 0 < float(tolerance) <= MAX_MEMBERSHIP_TOLERANCE_M):
            errors.append("assignment_policy.numeric_tolerance_m must be in (0, "
                          + str(MAX_MEMBERSHIP_TOLERANCE_M) + "]")
    routing = overlay.get("routing_policy")
    if not isinstance(routing, dict) or routing.get("search") != "bfs_min_edge_count" \
            or routing.get("metric_distance_available") is not False:
        errors.append("routing_policy must declare bfs_min_edge_count and no metric distance")


def _check_regions(overlay, errors):
    seen_ids, seen_floors = set(), {}
    for region in overlay.get("regions") or []:
        rid = region.get("region_id")
        if not (isinstance(rid, str) and rid.startswith("region_") and len(rid) > len("region_")):
            errors.append("Invalid region_id: " + repr(rid))
            continue
        if rid in seen_ids:
            errors.append("Duplicate region_id: " + rid)
        seen_ids.add(rid)
        if region.get("region_type") not in REGION_TYPES:
            errors.append(rid + ".region_type is invalid")
        if not (isinstance(region.get("source_room_name"), str) and region["source_room_name"].strip()):
            errors.append(rid + ".source_room_name must be a nonempty string")
        if not isinstance(region.get("label_conflict"), bool):
            errors.append(rid + ".label_conflict must be a boolean")
        floor_id = region.get("floor_object_id")
        if not isinstance(floor_id, int) or isinstance(floor_id, bool):
            errors.append(rid + ".floor_object_id must be an integer")
        elif floor_id in seen_floors:
            errors.append("floor_object_id " + str(floor_id) + " is claimed by "
                          + seen_floors[floor_id] + " and " + rid)
        else:
            seen_floors[floor_id] = rid
        if _rect(region.get("floor_rect_xy")) is None:
            errors.append(rid + ".floor_rect_xy must be [min_x, min_y, max_x, max_y]")
        if region.get("floor_polygon_xy") is not None:
            errors.append(rid + ".floor_polygon_xy must be null in this overlay version")
        match = region.get("frame_floor_match")
        if not isinstance(match, dict) or match.get("status") not in (
                "within_tolerance", "mismatch", "unresolved"):
            errors.append(rid + ".frame_floor_match.status is invalid")
    return seen_ids


def _check_region_disjointness(overlay, errors):
    """Unique membership is only meaningful if the rectangles do not overlap."""
    try:
        rects = region_rects(overlay)
    except OverlayError as exc:
        errors.append(str(exc))
        return
    tolerance = float(overlay["assignment_policy"]["numeric_tolerance_m"])
    ids = sorted(rects)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            if rect_overlaps(rects[left], rects[right], tolerance):
                errors.append("Region rectangles overlap: " + left + " and " + right)


def _check_connections(overlay, region_ids, errors):
    seen = set()
    for connection in overlay.get("connections") or []:
        pid = connection.get("portal_id")
        if not (isinstance(pid, str) and pid.startswith("portal_") and len(pid) > len("portal_")):
            errors.append("Invalid portal_id: " + repr(pid))
            continue
        if pid in seen:
            errors.append("Duplicate portal_id: " + pid)
        seen.add(pid)
        pair = connection.get("regions")
        if not (isinstance(pair, list) and len(pair) == 2):
            errors.append(pid + ".regions must be a pair")
        elif pair[0] == pair[1]:
            errors.append(pid + " connects a region to itself")
        elif not all(r in region_ids for r in pair):
            errors.append(pid + " references an unknown region")
        if connection.get("portal_type") not in PORTAL_TYPES:
            errors.append(pid + ".portal_type is invalid")
        if connection.get("door_state") not in DOOR_STATES:
            errors.append(pid + ".door_state is invalid")
        if connection.get("derived_from_object_proximity") is not False:
            errors.append(pid + " must not be derived from object proximity")
        if connection.get("geometry_verified") is not False:
            errors.append(pid + ".geometry_verified must stay false")
        if connection.get("traversability") != "unknown":
            errors.append(pid + ".traversability must stay unknown")
        if connection.get("width_m") is not None or connection.get("length_m") is not None:
            errors.append(pid + " must not carry metric width/length")
        if not (isinstance(connection.get("evidence"), str) and connection["evidence"].strip()):
            errors.append(pid + ".evidence must be a nonempty string")


def _check_index(overlay, region_ids, errors):
    index = overlay.get("object_region_index")
    if not isinstance(index, list) or not index:
        errors.append("object_region_index must be a nonempty array")
        return {}
    by_id = {}
    for entry in index:
        oid = entry.get("object_id")
        if not isinstance(oid, int) or isinstance(oid, bool):
            errors.append("object_region_index object_id must be an integer: " + repr(oid))
            continue
        if oid in by_id:
            errors.append("Duplicate object_region_index entry for object " + str(oid))
            continue
        by_id[oid] = entry
        if entry.get("status") not in ("assigned", "unknown"):
            errors.append("object " + str(oid) + ".status is invalid")
        if entry.get("assignment_basis") not in ASSIGNMENT_BASES:
            errors.append("object " + str(oid) + ".assignment_basis is invalid")
        if entry.get("containment") not in CONTAINMENTS:
            errors.append("object " + str(oid) + ".containment is invalid")
        if entry.get("source_agreement") not in AGREEMENTS:
            errors.append("object " + str(oid) + ".source_agreement is invalid")
        overlapping = entry.get("overlapping_region_ids")
        if not isinstance(overlapping, list) or any(r not in region_ids for r in overlapping):
            errors.append("object " + str(oid) + ".overlapping_region_ids is invalid")
        if entry.get("status") == "assigned":
            if entry.get("region_id") not in region_ids:
                errors.append("object " + str(oid) + " is assigned to an unknown region")
            if entry.get("containment") != "fully_inside":
                errors.append("object " + str(oid) + " is assigned without full containment")
            if entry.get("assignment_basis") != "source_frame_geometry":
                # v1 is a strict full-bbox geometry policy: a candidate-derived
                # assignment would override the unknown-is-retained rule.
                errors.append("object " + str(oid) + " is assigned without source geometry; "
                              "candidate evidence may not assign a region in this overlay version")
        elif entry.get("region_id") is not None:
            errors.append("object " + str(oid) + " is unknown but carries a region_id")
    return by_id


def _check_coverage(overlay, errors):
    declared = overlay.get("coverage")
    if not isinstance(declared, dict):
        errors.append("coverage must be an object")
        return None
    recomputed = coverage_counters(overlay.get("object_region_index") or [])
    for key, value in recomputed.items():
        if declared.get(key) != value:
            errors.append("coverage." + key + " declares " + repr(declared.get(key))
                          + " but the index contains " + repr(value))
    return recomputed


def _check_against_graph(overlay, nodes, by_id, errors):
    """Every node needs exactly one entry, and geometry must reproduce the claim."""
    node_ids = [node["id"] for node in nodes]
    missing = sorted(set(node_ids) - set(by_id))
    extra = sorted(set(by_id) - set(node_ids))
    if missing:
        errors.append("object_region_index is missing nodes: " + repr(missing[:12]))
    if extra:
        errors.append("object_region_index references absent nodes: " + repr(extra[:12]))
    for region in overlay.get("regions") or []:
        if region.get("floor_object_id") not in set(node_ids):
            errors.append(str(region.get("region_id")) + ".floor_object_id is not a graph node")
    recomputed = recompute_object_regions(nodes, overlay)
    mismatches = []
    for oid in sorted(set(node_ids) & set(by_id)):
        declared, actual = by_id[oid], recomputed[oid]
        basis = declared.get("assignment_basis")
        if basis == "source_frame_geometry":
            same = (declared.get("status") == actual["status"]
                    and declared.get("region_id") == actual["region_id"]
                    and declared.get("containment") == actual["containment"]
                    and sorted(declared.get("overlapping_region_ids") or [])
                    == sorted(actual["overlapping_region_ids"]))
            if not same:
                mismatches.append({"object_id": oid, "declared": declared, "recomputed": actual})
        elif declared.get("status") == "unknown" and actual["status"] == "assigned":
            mismatches.append({"object_id": oid, "reason":
                               "geometry decides this object but the overlay declares it unknown",
                               "declared": declared, "recomputed": actual})
    if mismatches:
        errors.append("Geometric recomputation disagrees with the overlay for "
                      + str(len(mismatches)) + " object(s); first: " + repr(mismatches[0]["object_id"]))
    return recomputed, mismatches


def load_overlay(path, nodes=None, scene_id=None):
    """Return ``(overlay, report)`` or raise OverlayError listing every defect found.

    ``nodes`` enables the independent geometric recomputation; without it only the
    structural checks run and the report records that fact.
    """
    overlay = read_json(path)
    if not isinstance(overlay, dict):
        raise OverlayError("Overlay must be a JSON object")
    errors = []
    _check_structure(overlay, errors)
    if errors:
        raise OverlayError("; ".join(errors))
    region_ids = _check_regions(overlay, errors)
    _check_region_disjointness(overlay, errors)
    _check_connections(overlay, region_ids, errors)
    by_id = _check_index(overlay, region_ids, errors)
    coverage = _check_coverage(overlay, errors)
    if scene_id is not None and overlay.get("scene_id") != scene_id:
        errors.append("Overlay scene_id " + repr(overlay.get("scene_id"))
                      + " does not match " + repr(scene_id))
    for region in overlay.get("regions") or []:
        if region.get("label_conflict") and not any(
                region["region_id"] in (item.get("affected_region_ids") or [])
                for item in overlay.get("unknowns") or []):
            errors.append(region["region_id"] + " declares a label conflict with no unknowns entry")
    recomputed, mismatches = (None, [])
    if nodes is not None and not errors:
        recomputed, mismatches = _check_against_graph(overlay, nodes, by_id, errors)
    if errors:
        raise OverlayError("; ".join(errors))
    report = {
        "overlay_version": overlay["overlay_version"],
        "overlay_sha256": sha256_file(path),
        "overlay_path": str(path),
        "scene_id": overlay["scene_id"],
        "region_count": len(overlay["regions"]),
        "connection_count": len(overlay["connections"]),
        "coverage_recomputed": coverage,
        "membership_tolerance_m": float(overlay["assignment_policy"]["numeric_tolerance_m"]),
        "geometry_recomputed": nodes is not None,
        "geometry_recomputation_mismatches": mismatches,
        "label_conflict_regions": sorted(r["region_id"] for r in overlay["regions"]
                                         if r.get("label_conflict")),
        "label_conflict_semantics": "Text-level difference between declared label sources; "
                                    "not a verified semantic contradiction.",
        "evidence_statement": "Source asset geometry index. Connections are structural wall-opening "
                              "evidence only: not visually verified, not a robot traversability map, "
                              "not ground truth.",
    }
    return overlay, report


def object_region_map(overlay):
    return {entry["object_id"]: entry.get("region_id") for entry in overlay["object_region_index"]}


def object_region_entry(overlay, object_id):
    for entry in overlay["object_region_index"]:
        if entry["object_id"] == object_id:
            return entry
    return None


def region_by_id(overlay, region_id):
    for region in overlay["regions"]:
        if region["region_id"] == region_id:
            return region
    return None


def verify_source_hashes(overlay, graph_path, edges_path):
    """Recompute the graph/edge digests and compare them with the overlay provenance.

    Presence of a hash string is not evidence; the bytes actually supplied to the
    filter must match the bytes the overlay was reviewed against.
    """
    actual = {"scene_graph_sha256": sha256_file(graph_path),
              "scene_graph_edges_sha256": sha256_file(edges_path)}
    declared = {key: overlay["provenance"].get(key) for key in actual}
    mismatched = sorted(key for key in actual if actual[key] != declared[key])
    if mismatched:
        raise OverlayError("Overlay provenance does not match the supplied graph bytes: "
                           + ", ".join(key + " declared=" + repr(declared[key]) + " actual="
                                       + repr(actual[key]) for key in mismatched))
    return {"declared": declared, "recomputed": actual, "match": True}


def verify_coordinate_frame(overlay, metadata):
    declared = metadata.get("coordinate_frame")
    if declared != overlay.get("coordinate_frame"):
        raise OverlayError("Coordinate frame mismatch: metadata=" + repr(declared)
                           + " overlay=" + repr(overlay.get("coordinate_frame")))
    if metadata.get("length_unit") != overlay.get("length_unit"):
        raise OverlayError("Length unit mismatch: metadata=" + repr(metadata.get("length_unit"))
                           + " overlay=" + repr(overlay.get("length_unit")))
    return declared
