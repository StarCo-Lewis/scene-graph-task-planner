"""Crop the scene graph to the routed regions plus the dependency closure.

The seed set is built first and the relation closure is taken over the whole seed
set, not just the task objects:

1. every task-role object;
2. every floor node of a routed region;
3. every object in a routed region;
4. every object whose region is unknown, globally: an unknown region is never
   treated as "outside scope", so the crop cannot turn an unknown into an absence
   claim.

The bidirectional transitive closure of that seed set is then retained, so a
retained object never loses a support or constraint partner. The closure can
reach into regions the route does not touch; that is the intended conservative
behaviour, and the resulting compression ratio is recorded rather than tuned.
Edges are the induced edges of the retained nodes, copied with their original
evidence flags: closure membership never raises an edge's evidence level and a
support relation is never a passage.
"""

SLICE_VERSION = "topology_subgraph_crop_v1"


def support_adjacency(edges):
    adjacency = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source, target = edge.get("source"), edge.get("target")
        if source is None or target is None:
            continue
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)
    return adjacency


def support_closure(seed_ids, edges):
    """Bidirectional transitive closure over supplied relations; evidence untouched."""
    adjacency = support_adjacency(edges)
    seen, frontier = set(seed_ids), list(seed_ids)
    while frontier:
        current = frontier.pop()
        for neighbour in sorted(adjacency.get(current, ())):
            if neighbour not in seen:
                seen.add(neighbour)
                frontier.append(neighbour)
    return seen


def build_crop(nodes, edges, overlay, keep_regions, task_object_ids, extra_keep=None):
    """Return ``(kept_nodes, kept_edges, audit)`` preserving original byte-level values.

    ``extra_keep`` is an optional set of extra node ids retained by the offline
    deterministic context check in :mod:`embodied_pipeline.topology_filter.expand`
    (a bound target, a suppressed competing candidate or a phrase competitor the
    crop would otherwise have hidden). The default is ``None``, and when nothing
    extra is requested the retained set, the retained reasons and every audit key
    are exactly what they were before this parameter existed.
    """
    assignments = {entry["object_id"]: entry for entry in overlay["object_region_index"]}
    keep_regions = set(keep_regions)
    floor_ids = {region["floor_object_id"] for region in overlay["regions"]
                 if region["region_id"] in keep_regions}
    task_ids = set(task_object_ids)
    extra_ids = set(extra_keep or ())
    seed_reasons = {}
    for node in nodes:
        node_id = node["id"]
        entry = assignments.get(node_id, {})
        if node_id in task_ids:
            seed_reasons[node_id] = "task_object"
        elif node_id in floor_ids:
            seed_reasons[node_id] = "routed_region_floor_node"
        elif entry.get("status") != "assigned":
            seed_reasons[node_id] = "region_unknown_retained_globally"
        elif entry.get("region_id") in keep_regions:
            seed_reasons[node_id] = "object_in_routed_region"
        elif node_id in extra_ids:
            # Lowest precedence: an existing reason is never relabelled, so the
            # default run is unchanged and only genuinely new ids gain a reason.
            seed_reasons[node_id] = "deterministic_context_retrieval"
    closure = support_closure(set(seed_reasons), edges)
    present = {node["id"] for node in nodes}
    reasons, kept_ids = {}, set()
    for node in nodes:
        node_id = node["id"]
        if node_id in seed_reasons:
            reasons[node_id] = seed_reasons[node_id]
            kept_ids.add(node_id)
        elif node_id in closure:
            reasons[node_id] = "relation_closure_of_retained_seed"
            kept_ids.add(node_id)
        else:
            reasons[node_id] = "dropped_region_outside_routed_scope"
    kept_nodes = [node for node in nodes if node["id"] in kept_ids]
    kept_edges, dropped_edges = [], []
    for edge in edges:
        source, target = (edge.get("source"), edge.get("target")) if isinstance(edge, dict) \
            else (None, None)
        if source in kept_ids and target in kept_ids:
            kept_edges.append(edge)
        else:
            dropped_edges.append({"source": source, "target": target,
                                  "relation": edge.get("relation") if isinstance(edge, dict) else None,
                                  "reason": "endpoint_not_retained"})
    retained_by_reason = {}
    for node_id in sorted(kept_ids):
        retained_by_reason.setdefault(reasons[node_id], []).append(node_id)
    audit = {
        "slice_version": SLICE_VERSION,
        "scene_id": overlay["scene_id"],
        "routed_regions": sorted(keep_regions),
        "task_object_ids": sorted(set(task_object_ids)),
        "seed_node_count": len(seed_reasons),
        "closure_added_node_count": len(kept_ids) - len(seed_reasons),
        "closure_referenced_absent_ids": sorted(closure - present),
        "retained_node_count": len(kept_nodes),
        "dropped_node_count": len(nodes) - len(kept_nodes),
        "retained_node_ids": sorted(kept_ids),
        "dropped_node_ids": sorted(set(node["id"] for node in nodes) - kept_ids),
        "retained_reasons": retained_by_reason,
        "dropped_reason": "dropped_region_outside_routed_scope",
        "retained_edge_count": len(kept_edges),
        "dropped_edge_count": len(dropped_edges),
        "dropped_edges": dropped_edges,
        "node_retention_ratio": (len(kept_nodes) / len(nodes)) if nodes else None,
        "edge_retention_ratio": (len(kept_edges) / len(edges)) if edges else None,
        "seed_policy": "Seeds are task objects, routed-region floor nodes, every object in a "
                       "routed region and every unknown-region object; the closure is taken over "
                       "the full seed set, so it may extend into regions the route does not touch.",
        "unknown_region_objects_retained": sorted(
            node_id for node_id in kept_ids
            if assignments.get(node_id, {}).get("status") != "assigned"),
        "edge_evidence_policy": "Edges keep their original evidence flags; closure membership never "
                                "raises an evidence level and a support relation is not a passage.",
        "absence_policy": "A node absent from this crop is outside the retained scope. That is not "
                          "evidence that the object is absent from the environment.",
    }
    if extra_ids:
        # Only present when an expansion actually happened, so a default crop keeps
        # its original audit keys and bytes.
        audit["context_retrieval"] = {
            "requested_extra_ids": sorted(extra_ids),
            "newly_retained_ids": sorted(node_id for node_id in extra_ids
                                         if seed_reasons.get(node_id)
                                         == "deterministic_context_retrieval"),
            "already_retained_ids": sorted(node_id for node_id in extra_ids
                                           if seed_reasons.get(node_id)
                                           not in (None, "deterministic_context_retrieval")),
            "absent_from_source_graph_ids": sorted(
                node_id for node_id in extra_ids
                if node_id not in {node["id"] for node in nodes}),
            "retrieval_trigger": "deterministic_pre_planning_context_check",
            "model_requests_made": 0,
            "selection_basis": "byte-for-byte lookup by id in the locked complete graph; no "
                               "node created, no field edited, no region assigned",
        }
    return kept_nodes, kept_edges, audit


def crop_metadata(source_metadata, audit, provenance):
    """Derived metadata for the cropped scene; no 'complete input' claim survives."""
    metadata = dict(source_metadata)
    metadata["input_filtering_provenance"] = {
        "input_graph_filtered": True,
        "graph_retrieval_or_cropping": True,
        "filter_tool": provenance["filter_tool"],
        "filter_version": SLICE_VERSION,
        "filter_run_id": provenance["filter_run_id"],
        "overlay_sha256": provenance["overlay_sha256"],
        "full_graph_sha256": provenance["full_graph_sha256"],
        "full_graph_edges_sha256": provenance["full_graph_edges_sha256"],
        "full_graph_node_count": provenance["full_graph_node_count"],
        "full_graph_edge_count": provenance["full_graph_edge_count"],
        "retained_node_count": audit["retained_node_count"],
        "dropped_node_count": audit["dropped_node_count"],
        "retained_edge_count": audit["retained_edge_count"],
        "dropped_edge_count": audit["dropped_edge_count"],
        "routed_regions": audit["routed_regions"],
        "unknown_region_objects_retained": len(audit["unknown_region_objects_retained"]),
        "overlay_is_ground_truth": False,
        "robot_execution_verified": False,
        "absence_claim_not_supported": True,
        "statement": "This scene directory is a cropped subgraph selected offline by the topology "
                     "input filter. It is not the complete original graph and not a complete object "
                     "inventory.",
    }
    metadata["object_inventory_complete"] = False
    metadata["scene_graph_scope"] = "cropped_subgraph"
    return metadata
