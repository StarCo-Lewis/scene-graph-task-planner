"""Build the cropped-scope task handed to the existing collection worker.

The instruction is carried through byte for byte. Everything the filter decided
travels in the separate ``topology_task_context`` structure, never by rewriting
the instruction text.
"""

from . import CROPPED_SCOPE, PIPELINE_VERSION
from .binding import BINDING_VERSION, SUPPORTED_TASK_CLASS, prebinding_contract_text
from .route import ROUTE_VERSION

CONTEXT_VERSION = "topology_task_context_v1"
SCENE_FILES = ("scene_graph.json", "scene_graph_edges.json", "scene_metadata.json")
# One external prebinding request plus the three requests the existing worker makes.
PREBINDING_MODEL_REQUESTS = 1
DOWNSTREAM_MODEL_REQUESTS = 3
#: Opt-in public task input. These are the keys the existing reviewed-context
#: channel in ``embodied_pipeline/data_collection/context_contract.py`` validates
#: and forwards to all three worker requests. They are public collection input,
#: never a reference answer, and the task carries them unchanged.
#:
#: The cropped-scope profile adds the anchor attestation, because the user-location
#: object may be outside the subgraph this filter produced. Its node-level checks
#: therefore happen once, at prepare, against the complete source graph.
PUBLIC_CONTEXT_KEYS = ("collection_public_context_version", "model_input",
                       "conversation_context", "public_spatial_context")
PUBLIC_CONTEXT_ATTESTATION_KEY = "public_context_source_graph_attestation"
PUBLIC_CONTEXT_KEY_SETS = (frozenset(PUBLIC_CONTEXT_KEYS),
                           frozenset(PUBLIC_CONTEXT_KEYS + (PUBLIC_CONTEXT_ATTESTATION_KEY,)))


def build_context(prebinding, route_plan, slice_audit, start, initial_state, overlay_report,
                  prebinding_provenance, graph_provenance):
    # Hashes and counts are validated apart: a graph with zero edges is legitimate,
    # so a falsy-value test would wrongly reject full_graph_edge_count == 0.
    for key in ("full_graph_sha256", "full_graph_edges_sha256"):
        value = graph_provenance.get(key)
        if not (isinstance(value, str) and value.strip()):
            raise ValueError("Cropped-scope context requires a nonempty hash for: " + key)
    for key, minimum in (("full_graph_node_count", 1), ("full_graph_edge_count", 0)):
        value = graph_provenance.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError("Cropped-scope context requires an integer >= " + str(minimum)
                             + " for: " + key)
    roles = {role["role_id"]: role for role in prebinding["roles"]}
    prebound = [{"role_id": role["role_id"], "role_kind": role["role_kind"],
                 "role_text": role["role_text"], "expected_labels": role["expected_labels"],
                 "object_id": role.get("object_id"), "region_id": role.get("region_id"),
                 "status": role["status"], "binding_basis": "prebinding_on_complete_graph_index"}
                for role in prebinding["roles"]]
    visit_role_ids = list(prebinding["visit_role_ids"])
    return {
        "context_version": CONTEXT_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "scope": "cropped_subgraph",
        "task_class": SUPPORTED_TASK_CLASS,
        "role_contract": prebinding_contract_text(),
        "role_order_is_not_action_order": True,
        "prebinding_version": BINDING_VERSION,
        "prebinding_provenance": prebinding_provenance,
        "prebound_roles": prebound,
        "ordered_visit_role_ids": visit_role_ids,
        "ordered_visit_object_ids": [roles[rid].get("object_id") for rid in visit_role_ids],
        "terminal_role_id": prebinding.get("terminal_role_id"),
        "terminal_robot_location_required": bool(
            prebinding.get("terminal_robot_location_required")),
        "terminal_semantics": "terminal_role_id is only the last visit target. A terminal robot "
                              "location is expected only when terminal_robot_location_required "
                              "is true.",
        "start": start,
        "initial_state": initial_state,
        "route": {
            "route_version": ROUTE_VERSION,
            "start_region": route_plan["start_region"],
            "start_region_basis": route_plan["start_region_basis"],
            "ordered_waypoint_regions": route_plan["ordered_waypoint_regions"],
            "legs": [{"leg_index": leg["leg_index"], "role_id": leg["role_id"],
                      "object_id": leg["object_id"], "from_region": leg["from_region"],
                      "to_region": leg["to_region"], "path_regions": leg["path_regions"],
                      "portal_ids": leg["portal_ids"], "hop_count": leg["hop_count"],
                      "traversability": leg["traversability"], "length_m": None}
                     for leg in route_plan["legs"]],
            "total_hop_count": route_plan["total_hop_count"],
            "search": route_plan["search"],
            "metric_distance_computed": False,
            "distance_semantics": route_plan["distance_semantics"],
            "statement": route_plan["statement"],
        },
        "overlay_sha256": overlay_report["overlay_sha256"],
        "full_graph_sha256": graph_provenance["full_graph_sha256"],
        "full_graph_edges_sha256": graph_provenance["full_graph_edges_sha256"],
        "full_graph_node_count": graph_provenance["full_graph_node_count"],
        "full_graph_edge_count": graph_provenance["full_graph_edge_count"],
        "retained_node_count": slice_audit["retained_node_count"],
        "dropped_node_count": slice_audit["dropped_node_count"],
        "node_retention_ratio": slice_audit.get("node_retention_ratio"),
        "retention_notice": "Objects whose region is unknown were retained globally. An object "
                            "absent from the supplied subgraph is outside the retained scope; that "
                            "is not evidence of absence from the environment. Report missing "
                            "environment information instead of asserting absence.",
        "evidence_status": "structural_source_geometry_only_not_physically_verified",
        "prebinding_consistency_gate": "enforced_before_any_skill_binding_request",
        "model_request_accounting": {
            "external_prebinding_requests": PREBINDING_MODEL_REQUESTS,
            "downstream_worker_requests": DOWNSTREAM_MODEL_REQUESTS,
            "pipeline_total_expected": PREBINDING_MODEL_REQUESTS + DOWNSTREAM_MODEL_REQUESTS,
            "note": "The cropped scope adds one prebinding request on top of the three requests "
                    "the existing worker makes, so the pipeline total is four, not three.",
        },
    }


def build_task(record_id, scene_id, instruction, scene_hashes, context, public_context=None):
    """Assemble the cropped-scope task.

    ``public_context`` is optional and defaults to absent, so every existing caller
    produces the same task as before. When supplied it must contain exactly
    :data:`PUBLIC_CONTEXT_KEYS`, and its own copy of the instruction must be the
    same bytes as the instruction being handed over: the worker's reviewed-context
    channel rejects a mismatch, and rewriting the user's sentence is not permitted
    anywhere in this pipeline.
    """
    if set(scene_hashes) != set(SCENE_FILES):
        raise ValueError("Cropped task requires exactly the three scene file hashes")
    task = {
        "record_id": record_id,
        "scene_id": scene_id,
        "instruction": instruction,
        "input_scope": CROPPED_SCOPE,
        "prepared_scene_sha256": dict(scene_hashes),
        "topology_task_context": context,
    }
    if public_context is None:
        return task
    if not isinstance(public_context, dict) \
            or frozenset(public_context) not in PUBLIC_CONTEXT_KEY_SETS:
        raise ValueError("Public task input must carry exactly "
                         + ", ".join(PUBLIC_CONTEXT_KEYS) + ", optionally plus "
                         + PUBLIC_CONTEXT_ATTESTATION_KEY)
    model_input = public_context["model_input"]
    if not isinstance(model_input, dict) or model_input.get("instruction") != instruction:
        raise ValueError("Public task input must repeat the handed-over instruction "
                         "byte for byte")
    for key in sorted(public_context):
        task[key] = public_context[key]
    return task
