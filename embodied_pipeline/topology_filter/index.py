"""Complete-graph lightweight index used for prebinding (nothing is truncated).

Prebinding must see every candidate for an ambiguous phrase, so this index covers
all nodes and all supplied edges. Only verbose per-node evidence blobs are left
out; identity, label, caption, geometry and relations are kept verbatim.
"""

import re

INDEX_VERSION = "topology_lightweight_index_v1"
# Identity, geometry, relation and uncertainty evidence are always carried over.
NODE_FIELDS = ("id", "object_tag", "caption", "bbox_center", "bbox_extent", "active",
               "semantic_status", "instance_identity_verified", "uncertainty_reason")
# Optional discriminating attributes: kept verbatim whenever the node declares them,
# because dropping them would remove disambiguation evidence before binding.
OPTIONAL_NODE_FIELDS = ("attributes", "color", "colour", "material", "size", "shape", "state",
                        "affordances", "openable", "container", "support_surface", "room",
                        "region", "parent_id", "part_of", "object_state", "properties")


def node_record(node):
    record = {field: node.get(field) for field in NODE_FIELDS}
    for field in OPTIONAL_NODE_FIELDS:
        if field in node and node[field] not in (None, [], {}, ""):
            record[field] = node[field]
    return record


def edge_flag_counts(edges):
    """Observed evidence flags, so the index reports the input instead of asserting it."""
    counts = {}
    for flag in ("verified", "physics_verified"):
        tally = {}
        for edge in edges:
            value = edge.get(flag, "absent") if isinstance(edge, dict) else "absent"
            tally[repr(value)] = tally.get(repr(value), 0) + 1
        counts[flag] = tally
    return counts


def normalize_label(text):
    if not isinstance(text, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def label_groups(nodes):
    """Group ids by normalized label so a duplicate label is visible before binding."""
    groups = {}
    for node in nodes:
        groups.setdefault(normalize_label(node.get("object_tag")), []).append(node["id"])
    return {label: sorted(ids) for label, ids in sorted(groups.items()) if label and len(ids) > 1}


def build_index(nodes, edges, overlay):
    """Return the full-graph index plus the region annotation from the overlay."""
    assignments = {entry["object_id"]: entry for entry in overlay["object_region_index"]}
    duplicates = label_groups(nodes)
    duplicate_of = {}
    for label, ids in duplicates.items():
        for node_id in ids:
            duplicate_of[node_id] = label
    objects = []
    for node in sorted(nodes, key=lambda item: item["id"]):
        entry = assignments.get(node["id"], {})
        record = node_record(node)
        record.update(
            region_id=entry.get("region_id"),
            region_status=entry.get("status", "unknown"),
            region_assignment_basis=entry.get("assignment_basis", "unknown"),
            region_containment=entry.get("containment", "no_geometry"),
            duplicate_label_group=duplicate_of.get(node["id"]))
        objects.append(record)
    return {
        "index_version": INDEX_VERSION,
        "scene_id": overlay["scene_id"],
        "coordinate_frame": overlay["coordinate_frame"],
        "length_unit": overlay["length_unit"],
        "object_count": len(objects),
        "objects": objects,
        "edges": list(edges),
        "edge_count": len(edges),
        "duplicate_label_groups": duplicates,
        "edge_evidence_flag_counts": edge_flag_counts(edges),
        "regions": [{"region_id": region["region_id"],
                     "source_room_name": region["source_room_name"],
                     "visual_label_from_graph": region.get("visual_label_from_graph"),
                     "author_label_legacy": region.get("author_label_legacy"),
                     "label_conflict": region.get("label_conflict"),
                     "region_type": region["region_type"],
                     "floor_object_id": region["floor_object_id"]}
                    for region in overlay["regions"]],
        "coverage": overlay["coverage"],
        "completeness": "All graph nodes and all supplied edges are present; no candidate was "
                        "dropped for binding.",
        "edge_evidence_statement": "Edges are copied with their original evidence flags; see "
                                   "edge_evidence_flag_counts for what the input actually declares. "
                                   "This filter never upgrades an edge's evidence level, and a "
                                   "support relation is never treated as a passage.",
        "region_statement": "Region annotation is a source-asset geometry index; objects with "
                            "region_status 'unknown' have no guessed room.",
    }
