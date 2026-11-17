"""Controlled-template skeleton and validation of one current model binding.

No whole-task target resolver exists here. Parsing never consults scene labels,
references or routes. Semantic binding remains a model proposal, not ground truth.
"""

import re

from . import PLAN_VERSION
from ..contracts import text_budget
from ..topology_filter import route as route_module

GLOBAL_TEMPLATE = ("First, go to the {first}; then go to the {next}; ...; "
                   "finally, return to the {first}.")
LOCAL_TEMPLATE = "Go to the {phrase}."
ROLE_TEXT_TEMPLATE = "the {phrase}"
LOCAL_ROLE_ID = "role_001"
TASK_CLASS = "ordered_navigation_visits_only"
_PHRASE = r"[a-z][a-z \-]*"
#: Named phrase grammars. The default is the original controlled one and stays
#: byte-identical for every existing caller, which passes no grammar at all.
#:
#: ``PHRASE_GRAMMAR_VERBATIM_SPAN`` is an opt-in profile for author-written
#: instructions that were collected before this template existed. It widens only
#: the character class - capitals, digits, round brackets and apostrophes - so a
#: noun phrase such as "long white table in the office (ID 133)" survives byte for
#: byte. It deliberately does not widen the clause grammar: ``_EXTRA_CLAUSE_RE``
#: still rejects a second action, and commas and full stops are still excluded, so
#: a phrase can never swallow a following clause. Widening the class is not a
#: licence to drop an author-supplied identifier or attribute: the opt-in profile
#: exists so those characters can be carried through unchanged instead.
PHRASE_GRAMMAR_DEFAULT = "bounded_lowercase_noun_phrase_v1"
PHRASE_GRAMMAR_VERBATIM_SPAN = "verbatim_author_noun_phrase_span_v1"
_VERBATIM_PHRASE = r"[A-Za-z][A-Za-z0-9 \-()'’]*"
PHRASE_GRAMMARS = (PHRASE_GRAMMAR_DEFAULT, PHRASE_GRAMMAR_VERBATIM_SPAN)
_PHRASE_PATTERNS = {PHRASE_GRAMMAR_DEFAULT: _PHRASE,
                    PHRASE_GRAMMAR_VERBATIM_SPAN: _VERBATIM_PHRASE}
_FIRST_RE = re.compile(r"First, go to the (" + _PHRASE + r")")
_MIDDLE_RE = re.compile(r"then go to the (" + _PHRASE + r")")
_FINAL_RE = re.compile(r"finally, return to the (" + _PHRASE + r")")
_LOCAL_RE = re.compile(r"Go to the (" + _PHRASE + r")\.")
_LOCAL_RES = {name: re.compile(r"Go to the (" + pattern + r")\.")
              for name, pattern in _PHRASE_PATTERNS.items()}
# This bounded grammar excludes additional clauses, not words like "open" in names.
_EXTRA_CLAUSE_RE = re.compile(
    r"\b(and|then|while|before|after|until|unless|when|but|also|to)\b")
BINDING_FIELDS = {
    "visit_event_id", "status", "object_id", "region_id", "candidate_object_ids",
    "evidence", "clarification_question", "candidate_evidence",
}
#: ``evidence`` is optional since 2026-11-08: omit it or give one short sentence. It is
#: never a reason to accept or refuse a binding and it is moved out of the engineering
#: JSON into an audit log. Every other field stays required.
OPTIONAL_BINDING_FIELDS = {"evidence"}
REQUIRED_BINDING_FIELDS = BINDING_FIELDS - OPTIONAL_BINDING_FIELDS
CANDIDATE_REQUIRED_FIELDS = {"object_id"}
CANDIDATE_ALLOWED_FIELDS = {"object_id", "why_still_fits"}
EVIDENCE_POLICY = "optional_short_sentence_no_length_limit_moved_to_audit_log_v1_20261108"
_SHORT_SENTENCE = ("optional; omit it, or give one short sentence naming the index fields "
                   "you relied on. It is not checked for length and never decides "
                   "acceptance; the structured fields carry the decision")
BIND_NEXT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": sorted(REQUIRED_BINDING_FIELDS),
    "properties": {
        "visit_event_id": {"type": "string"},
        "status": {"enum": ["bound", "ambiguous", "needs_environment_context"]},
        "object_id": {"type": ["integer", "null"]},
        "region_id": {"type": ["string", "null"]},
        "candidate_object_ids": {"type": "array", "uniqueItems": True,
                                 "items": {"type": "integer"}},
        "evidence": {"type": ["string", "null"], "description": _SHORT_SENTENCE},
        "clarification_question": {"type": ["string", "null"], "minLength": 1,
                                   "maxLength": 700},
        "candidate_evidence": {
            "type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": sorted(CANDIDATE_REQUIRED_FIELDS),
                "properties": {"object_id": {"type": "integer"},
                               "why_still_fits": {
                                   "type": ["string", "null"],
                                   "description": _SHORT_SENTENCE}}}},
    },
    "oneOf": [
        {"properties": {"status": {"const": "bound"}, "object_id": {"type": "integer"},
                        "region_id": {"type": "string"},
                        "candidate_object_ids": {"maxItems": 0},
                        "candidate_evidence": {"maxItems": 0},
                        "clarification_question": {"type": "null"}}},
        {"properties": {"status": {"const": "bound"}, "object_id": {"type": "integer"},
                        "region_id": {"type": "null"},
                        "candidate_object_ids": {"maxItems": 0},
                        "candidate_evidence": {"maxItems": 0},
                        "clarification_question": {"type": "string"}}},
        {"properties": {"status": {"const": "ambiguous"}, "object_id": {"type": "null"},
                        "region_id": {"type": "null"},
                        "candidate_object_ids": {"minItems": 2},
                        "candidate_evidence": {"minItems": 2},
                        "clarification_question": {"type": "string"}}},
        {"properties": {"status": {"const": "needs_environment_context"},
                        "object_id": {"type": "null"}, "region_id": {"type": "null"},
                        "candidate_object_ids": {"maxItems": 0},
                        "candidate_evidence": {"maxItems": 0},
                        "clarification_question": {"type": "string"}}},
    ],
}


class PlanRefused(ValueError):
    """The input is outside the controlled grammar or the binding contract."""

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _phrase_pattern(grammar):
    pattern = _PHRASE_PATTERNS.get(grammar)
    if pattern is None:
        raise PlanRefused("Unknown phrase grammar: " + repr(grammar))
    return pattern


def _check_phrase(phrase, grammar=PHRASE_GRAMMAR_DEFAULT):
    if (not isinstance(phrase, str) or not re.fullmatch(_phrase_pattern(grammar), phrase)
            or phrase.strip() != phrase or "  " in phrase
            or _EXTRA_CLAUSE_RE.search(phrase)):
        raise PlanRefused("Target phrase is outside the bounded noun-phrase grammar")
    return phrase


def render_global_instruction(phrases):
    if len(phrases) < 3 or phrases[0] != phrases[-1]:
        raise PlanRefused("At least three visits; the final phrase must repeat the first")
    for phrase in phrases:
        _check_phrase(phrase)
    parts = ["First, go to the " + phrases[0]]
    parts += ["then go to the " + phrase for phrase in phrases[1:-1]]
    parts.append("finally, return to the " + phrases[-1])
    return "; ".join(parts) + "."


def parse_global_instruction(instruction):
    if not isinstance(instruction, str) or not instruction.endswith("."):
        raise PlanRefused("Expected the exact global template ending in a full stop")
    segments = instruction[:-1].split("; ")
    if len(segments) < 3:
        raise PlanRefused("The global template needs at least three clauses")
    patterns = [_FIRST_RE] + [_MIDDLE_RE] * (len(segments) - 2) + [_FINAL_RE]
    phrases = []
    for segment, pattern in zip(segments, patterns):
        match = pattern.fullmatch(segment)
        if match is None:
            raise PlanRefused("Unexpected action or clause outside the navigation template")
        phrases.append(_check_phrase(match.group(1)))
    if render_global_instruction(phrases) != instruction:
        raise PlanRefused("Instruction must round-trip byte-exactly through the template")
    return phrases


def render_local_instruction(phrase, grammar=PHRASE_GRAMMAR_DEFAULT):
    return LOCAL_TEMPLATE.format(phrase=_check_phrase(phrase, grammar))


def parse_local_instruction(instruction, grammar=PHRASE_GRAMMAR_DEFAULT):
    expression = _LOCAL_RES.get(grammar)
    if expression is None:
        raise PlanRefused("Unknown phrase grammar: " + repr(grammar))
    match = expression.fullmatch(instruction)
    if match is None:
        raise PlanRefused("Expected exactly 'Go to the <phrase>.'")
    return _check_phrase(match.group(1), grammar)


def assert_navigation_only(phrases, statement="", grammar=PHRASE_GRAMMAR_DEFAULT):
    """Check action structure. Object-name modifiers are never treated as actions."""
    for phrase in phrases:
        _check_phrase(phrase, grammar)
    if statement:
        parsed = ([parse_local_instruction(statement)] if statement.startswith("Go to ")
                  else parse_global_instruction(statement))
        if list(phrases) != parsed:
            raise PlanRefused("Phrase sequence does not equal the template's sequence")
    return {"task_class": TASK_CLASS, "action_structure": "controlled_go_return_template",
            "manipulation_supported": False, "holding_state_supported": False,
            "protection_duty_supported": False, "generic_memory_supported": False}


def role_text_for(phrase, grammar=PHRASE_GRAMMAR_DEFAULT):
    return ROLE_TEXT_TEMPLATE.format(phrase=_check_phrase(phrase, grammar))


def visit_phrase_grammar(visit):
    """The grammar a visit declares, defaulting to the original controlled one.

    A visit produced by :func:`visit_skeleton` carries no grammar key, so every
    existing path keeps the default and its rendered strings are unchanged.
    """
    grammar = dict(visit).get("phrase_grammar", PHRASE_GRAMMAR_DEFAULT)
    _phrase_pattern(grammar)
    return grammar


def visit_skeleton(phrases):
    events, seen = [], {}
    for index, phrase in enumerate(phrases):
        _check_phrase(phrase)
        key = normalize(phrase)
        events.append({
            "visit_event_id": "visit_%03d" % (index + 1), "ordinal": index + 1,
            "phrase": phrase, "local_instruction": render_local_instruction(phrase),
            "role_text": role_text_for(phrase), "is_revisit": key in seen,
            "first_visit_event_id": seen.get(key), "is_final_visit": index == len(phrases) - 1,
        })
        seen.setdefault(key, events[-1]["visit_event_id"])
    return {"plan_version": PLAN_VERSION, "visit_count": len(events), "visits": events,
            "revisits_preserved": True}


def task_skeleton(instruction, start_region):
    phrases = parse_global_instruction(instruction)
    return {
        "plan_version": PLAN_VERSION, "task_class": TASK_CLASS,
        "capability": assert_navigation_only(phrases, instruction),
        "original_instruction": instruction, "parsed_phrases": phrases,
        "parse_basis": "controlled_template_not_general_language_understanding",
        "start_region": start_region, "start_region_basis": "scenario_assumption_not_observation",
        "skeleton": visit_skeleton(phrases), "reference_files_consulted": False,
        "target_bindings_precomputed": False,
    }


def binding_evidence_diagnostic(document):
    """Measurement only; never truncate or copy the reasoning into an error message.

    Since 2026-11-08 nothing refuses a binding because of this result. It is kept so
    that historical refusals made under the v1 word budget can still be recomputed and
    classified; :func:`validate_current_binding` no longer calls it.
    """
    evidence = document.get("evidence") if isinstance(document, dict) else None
    if not isinstance(evidence, str):
        return None
    return text_budget.diagnostic(evidence, "evidence")


def validate_current_binding(document, visit, nodes, overlay):
    """Validate references and state, without replacing a model's chosen source id.

    Counts from lexical matching are not an ambiguity oracle. The model must consider
    labels, captions and room facts and explain its status. These checks establish
    only a structurally consistent proposal; semantic correctness needs later review.
    """
    if not isinstance(document, dict) or not (
            REQUIRED_BINDING_FIELDS <= set(document) <= BINDING_FIELDS):
        raise PlanRefused("Current binding must have the declared contract fields "
                          "(evidence is optional) and no others")
    if document["visit_event_id"] != visit["visit_event_id"]:
        raise PlanRefused("Binding belongs to another visit")
    if not isinstance(document.get("evidence"), (str, type(None))):
        raise PlanRefused("Binding evidence, when present, must be text or null")
    by_id = {node["id"]: node for node in nodes}
    entries = {row["object_id"]: row for row in overlay["object_region_index"]}
    regions = {row["region_id"] for row in overlay["regions"]}

    def source_node(value):
        if type(value) is not int or value not in by_id:
            raise PlanRefused("Binding id must be an integer source object id")
        if by_id[value].get("active", True) is not True:
            raise PlanRefused("Inactive or malformed-active source object cannot be bound")
        return by_id[value]

    candidates = document["candidate_object_ids"]
    if not isinstance(candidates, list):
        raise PlanRefused("candidate_object_ids must be a list")
    for value in candidates:
        source_node(value)
    if len(set(candidates)) != len(candidates):
        raise PlanRefused("Candidate ids must be distinct")
    candidate_evidence = document["candidate_evidence"]
    if not isinstance(candidate_evidence, list):
        raise PlanRefused("candidate_evidence must be a list")
    question = document["clarification_question"]
    if question is not None and (
            not isinstance(question, str) or not question.strip() or len(question) > 700):
        raise PlanRefused("A question must be nonempty text of at most 700 characters")
    status = document["status"]
    common = {"model_proposal": document, "semantic_correctness_verified": False,
              "verification_basis": "source_id_active_and_recorded_region_consistency_only",
              "lexical_matcher_used_as_authority": False, "absence_not_asserted": True,
              "active_default_when_unspecified": True}
    if status == "bound":
        node = source_node(document["object_id"])
        entry = entries.get(node["id"], {})
        region = entry.get("region_id") if entry.get("status") == "assigned" else None
        if region is not None and region not in regions:
            raise PlanRefused("Overlay assignment refers to an unknown region")
        if document["region_id"] != region:
            raise PlanRefused("Model room must equal the source overlay, including null")
        if candidates or candidate_evidence:
            raise PlanRefused("A bound object has no ambiguity candidates")
        if (region is None and not question) or (region is not None and question is not None):
            raise PlanRefused("Only a bound object with an unknown room needs an environment "
                              "question; preserve that model question")
        return {**common, "resolved": True, "object_id": node["id"],
                "object_tag": node.get("object_tag"), "region_id": region,
                "region_status": entry.get("status"), "stop": region is None,
                "stop_reason": "needs_environment_context" if region is None else None,
                "question": question,
                "detail": "Bound object has no recorded room" if region is None else None}
    if status not in ("ambiguous", "needs_environment_context"):
        raise PlanRefused("Unknown binding status")
    if document["object_id"] is not None or document["region_id"] is not None or not question:
        raise PlanRefused("Unresolved binding needs null object/region and a concrete question")
    if status == "ambiguous":
        if len(candidates) < 2 or len(candidate_evidence) != len(candidates):
            raise PlanRefused("Ambiguity needs at least two distinct candidates with evidence")
        evidence_ids = []
        for item in candidate_evidence:
            if not isinstance(item, dict) or not (
                    CANDIDATE_REQUIRED_FIELDS <= set(item) <= CANDIDATE_ALLOWED_FIELDS):
                raise PlanRefused("Malformed per-candidate evidence")
            source_node(item["object_id"])
            if not isinstance(item.get("why_still_fits"), (str, type(None))):
                raise PlanRefused("why_still_fits, when present, must be text or null")
            evidence_ids.append(item["object_id"])
        if sorted(evidence_ids) != sorted(candidates):
            raise PlanRefused("Candidate evidence and candidate ids disagree")
    elif candidates or candidate_evidence:
        raise PlanRefused("Missing environment information is not a candidate list")
    return {**common, "resolved": False, "stop": True, "question": question,
            "stop_reason": ("clarification_required" if status == "ambiguous"
                            else "needs_environment_context"),
            "whole_task_stops": True, "candidate_object_ids": candidates}


def region_of(resolution):
    if not resolution.get("resolved") or resolution.get("region_status") != "assigned":
        return None
    return resolution.get("region_id") or None


def same_room(from_region, to_region):
    return bool(from_region and to_region and from_region == to_region)


def room_portal_graph(overlay, source_sha256):
    """Deterministic bipartite projection; no motion geometry or progress is invented."""
    region_ids = {row["region_id"] for row in overlay["regions"]}
    if len(region_ids) != len(overlay["regions"]):
        raise PlanRefused("Duplicate region id")
    nodes = [{"node_id": row["region_id"], "node_kind": "region",
              "source_region": dict(row), "source_overlay_sha256": source_sha256}
             for row in overlay["regions"]]
    edges, portals = [], set()
    for connection in overlay["connections"]:
        portal = connection["portal_id"]
        sides = connection["regions"]
        if (portal in portals or portal in region_ids or len(sides) != 2
                or sides[0] == sides[1] or any(side not in region_ids for side in sides)):
            raise PlanRefused("Malformed portal or portal endpoint")
        portals.add(portal)
        nodes.append({
            "node_id": portal, "node_kind": "portal", "portal_id": portal,
            "regions": list(sides), "source_connection": dict(connection),
            "door_state": connection.get("door_state", "unknown"),
            "traversability": connection.get("traversability", "unknown"),
            "evidence": connection.get("evidence"), "source_overlay_sha256": source_sha256,
            "width_m": connection.get("width_m"), "orientation_world": None,
            "safe_stopping_pose_by_region": {side: None for side in sides},
            "physical_crossing_acceptance_criterion": None,
            "sendable_navigation_goal": None,
        })
        for side in sides:
            for left, right in ((side, portal), (portal, side)):
                edges.append({
                    "from_node": left, "to_node": right, "portal_id": portal,
                    "regions": list(sides), "door_state": connection.get("door_state", "unknown"),
                    "traversability": connection.get("traversability", "unknown"),
                    "evidence": connection.get("evidence"),
                    "source_overlay_sha256": source_sha256, "length_m": None,
                })
    return {
        "graph_version": "room_portal_graph_v1", "source_overlay_sha256": source_sha256,
        "derivation": "one portal node per source connection; directed edges in both directions",
        "nodes": nodes, "edges": edges, "region_node_count": len(region_ids),
        "portal_node_count": len(portals), "contains_visit_progress": False,
        "routing_equivalence": "source region BFS; one region transition traverses two "
                               "bipartite edges via one portal",
        "metric_distances_available": False, "physical_traversability_verified": False,
    }


def plan_leg(overlay, from_region, to_region):
    """Only the current model-bound leg; BFS over actual connections, no assumed hub."""
    if not from_region or not to_region:
        return {"stop": True, "stop_reason": "needs_environment_context",
                "detail": "A leg endpoint has no recorded region",
                "from_region": from_region, "to_region": to_region}
    graph, excluded = route_module.build_region_graph(overlay)
    path = route_module.bfs_min_edge_path(graph, from_region, to_region)
    if path is None:
        return {"stop": True, "stop_reason": "needs_environment_context",
                "detail": "No recorded structural connection; physical absence not asserted",
                "from_region": from_region, "to_region": to_region}
    return {
        "stop": False, "from_region": from_region, "to_region": to_region,
        "same_room": same_room(from_region, to_region), **path,
        "room_transition_hops": path["hop_count"],
        "bipartite_edge_count": 2 * len(path["portal_ids"]),
        "intermediate_regions": path["path_regions"][1:-1],
        "excluded_closed_portals": excluded,
        "search": "bfs_min_edge_count_over_recorded_overlay_connections",
        "graph_shape_is_not_assumed": True, "corridor_not_inserted_for_direct_portals": True,
        "traversability": "unknown", "length_m": None, "metric_distance_computed": False,
        "statement": "Hop count is structural only; no physical traversal is verified.",
    }
