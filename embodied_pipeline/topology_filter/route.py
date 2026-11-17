"""Ordered region visits as a minimum-edge-count walk over the structural region graph.

This is never a metric shortest path and never a traversability claim: every
portal in the overlay carries ``traversability: "unknown"`` and no edge length
exists. Results are reported as hop counts (region transitions) only.
"""

from collections import deque

ROUTE_VERSION = "topology_region_route_v1"
DISTANCE_SEMANTICS = "graph_hop_count_only"
# A portal recorded as closed is structural adjacency, not a candidate route edge.
BLOCKED_DOOR_STATES = ("closed",)


class RouteError(ValueError):
    """Routing cannot proceed; the caller must stop instead of guessing a region."""


def build_region_graph(overlay):
    """Undirected adjacency ``{region_id: [(neighbor, portal_id), ...]}`` in stable order.

    Returns ``(graph, excluded)``. Portals whose ``door_state`` is closed are left
    out of the candidate edges and reported in ``excluded``: they remain recorded
    structural adjacency and must not be presented as a usable route.
    """
    graph = {region["region_id"]: [] for region in overlay["regions"]}
    excluded = []
    for connection in overlay["connections"]:
        left, right = connection["regions"]
        if connection.get("door_state") in BLOCKED_DOOR_STATES:
            excluded.append({"portal_id": connection["portal_id"], "regions": [left, right],
                             "door_state": connection.get("door_state"),
                             "reason": "door_state_closed_structural_adjacency_only"})
            continue
        graph[left].append((right, connection["portal_id"]))
        graph[right].append((left, connection["portal_id"]))
    return {region: sorted(set(neighbours)) for region, neighbours in graph.items()}, excluded


def bfs_min_edge_path(graph, start, goal):
    """Deterministic minimum-edge-count walk, or None when no structural link exists.

    Adjacency is sorted lexicographically by ``(neighbour_region_id, portal_id)``, so
    when several walks share the same minimum hop count the returned one is stable
    across runs. Only the hop count is minimised; no attempt is made to enumerate or
    rank equally long alternatives, and no metric criterion is applied.
    """
    if start not in graph or goal not in graph:
        raise RouteError("Unknown region in routing request: " + repr((start, goal)))
    if start == goal:
        return {"path_regions": [start], "portal_ids": [], "hop_count": 0}
    previous = {start: None}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for neighbour, portal_id in graph[current]:
            if neighbour in previous:
                continue
            previous[neighbour] = (current, portal_id)
            if neighbour == goal:
                regions, portals, node = [goal], [], goal
                while previous[node] is not None:
                    parent, portal_id = previous[node]
                    portals.append(portal_id)
                    regions.append(parent)
                    node = parent
                regions.reverse()
                portals.reverse()
                return {"path_regions": regions, "portal_ids": portals,
                        "hop_count": len(portals)}
            queue.append(neighbour)
    return None


def waypoint_regions(overlay, object_ids):
    """Map visit targets to regions; an unknown-region object stops routing.

    Objects whose region is unknown are never assigned a guessed room. The caller
    reports ``needs_environment_context`` and retains its evidence JSON.
    """
    assignments = {entry["object_id"]: entry for entry in overlay["object_region_index"]}
    resolved, unresolved = [], []
    for object_id in object_ids:
        entry = assignments.get(object_id)
        if entry is None:
            unresolved.append({"object_id": object_id, "reason": "object_not_in_overlay_index"})
        elif entry.get("status") != "assigned" or not entry.get("region_id"):
            unresolved.append({"object_id": object_id, "reason": "region_unknown",
                               "containment": entry.get("containment"),
                               "overlapping_region_ids": entry.get("overlapping_region_ids") or []})
        else:
            resolved.append({"object_id": object_id, "region_id": entry["region_id"]})
    return resolved, unresolved


def plan_route(overlay, start_region, ordered_waypoints):
    """Plan consecutive legs. ``ordered_waypoints`` keeps revisits as separate legs.

    A leg whose source and destination region are identical is retained with
    ``hop_count: 0`` so a revisit is never silently collapsed away.
    """
    graph, excluded_closed = build_region_graph(overlay)
    if start_region not in graph:
        raise RouteError("Start region is not in the overlay: " + repr(start_region))
    legs, unreachable = [], []
    current = start_region
    visited_in_order = [start_region]
    for waypoint in ordered_waypoints:
        region = waypoint["region_id"]
        path = bfs_min_edge_path(graph, current, region)
        if path is None:
            unreachable.append({"from_region": current, "to_region": region,
                                "object_id": waypoint.get("object_id"),
                                "reason": "no_structural_connection_in_overlay"})
            break
        legs.append({
            "leg_index": len(legs) + 1,
            "object_id": waypoint.get("object_id"),
            "role_id": waypoint.get("role_id"),
            "from_region": current,
            "to_region": region,
            "path_regions": path["path_regions"],
            "portal_ids": path["portal_ids"],
            "hop_count": path["hop_count"],
            "region_count_in_path": len(path["path_regions"]),
            "search": "bfs_min_edge_count",
            "traversability": "unknown",
            "length_m": None,
        })
        visited_in_order.extend(path["path_regions"][1:])
        current = region
    return {
        "route_version": ROUTE_VERSION,
        "scene_id": overlay["scene_id"],
        "start_region": start_region,
        "start_region_basis": "scenario_assumption_supplied_by_caller",
        "start_region_statement": "The start region is a declared scenario assumption; it is not "
                                  "evidence of an actual robot location.",
        "ordered_waypoint_regions": [waypoint["region_id"] for waypoint in ordered_waypoints],
        "ordered_waypoint_object_ids": [waypoint.get("object_id") for waypoint in ordered_waypoints],
        "legs": legs,
        "unreachable_legs": unreachable,
        "excluded_closed_portals": excluded_closed,
        "regions_visited_in_order": visited_in_order,
        "total_hop_count": sum(leg["hop_count"] for leg in legs),
        "complete": not unreachable and len(legs) == len(ordered_waypoints),
        "search": "bfs_min_edge_count",
        "tie_break": "stable_lexicographic_neighbour_then_portal_id",
        "metric_distance_computed": False,
        "distance_semantics": DISTANCE_SEMANTICS,
        "statement": "hop_count counts region transitions in the structural overlay graph. It is "
                     "not a metric shortest distance and not a physical traversability claim; all "
                     "portals remain traversability 'unknown'. Portals recorded as closed are "
                     "excluded from the candidate edges and listed in excluded_closed_portals.",
    }


def route_regions(route_plan):
    """Every region the walk touches, used to seed the crop."""
    regions = {route_plan["start_region"]}
    for leg in route_plan["legs"]:
        regions.update(leg["path_regions"])
    return sorted(regions)
