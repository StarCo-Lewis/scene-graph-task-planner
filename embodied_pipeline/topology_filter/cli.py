"""Offline CLI for the topology input filter. No subcommand contacts a model.

Stages: prepare -> ingest-binding -> route -> slice -> handoff, plus an optional
post-hoc check-binding review. Each stage

* owns an exclusive directory and never overwrites retained evidence;
* re-verifies the mutable sources (graph, edges, metadata, overlay) and the locked
  instruction against the digests prepare recorded;
* refuses to continue unless its predecessor completed successfully and its
  artifacts still hash to the recorded values;
* delivers engineering_plan.json with plan=null when it stops.
"""

import argparse
import copy
import json
from pathlib import Path
import sys

from . import CROPPED_SCOPE, PIPELINE_VERSION
from . import binding as binding_module
from . import handoff as handoff_module
from . import index as index_module
from . import overlay as overlay_module
from . import region_phrase as region_phrase_module
from . import route as route_module
from . import slice as slice_module
from . import stage as stage_module
from ..candidate_review import hooks as review_hooks

SCENE_GRAPH = "scene_graph.json"
SCENE_EDGES = "scene_graph_edges.json"
SCENE_METADATA = "scene_metadata.json"
SCENE_FILES = (SCENE_GRAPH, SCENE_EDGES, SCENE_METADATA)
# Revision markers for the model-facing text. They identify which wording and which
# output contract a run was prepared with; binding_version is deliberately unchanged,
# because the accepted document shape did not change, only its description.
PROMPT_REVISION = "prebinding_prompt_evidence_optional_short_20261108_v4"
CONTRACT_REVISION = "prebinding_contract_evidence_optional_short_20261108_v4"
#: Opt-in public task input, off by default. A task collected before this pipeline
#: existed can arrive with an authored user dialogue attached. That text is public
#: collection input and the worker already has one validated channel for it, so the
#: block is validated here with that same validator and then handed over unchanged.
#: It is not a second place to describe the scene and it carries no answer: the
#: validator rejects any key it does not know, and nothing here is derived from the
#: filter's own route, crop or prebinding.
PUBLIC_CONTEXT_FILE_KEYS = ("instruction", "model_input", "conversation_context",
                            "public_spatial_context")
PUBLIC_CONTEXT_CHANNEL = (
    "execution_contract.conversation_context + public_spatial_context, appended to "
    "factual_boundary by embodied_pipeline/data_collection/context_contract.py")
#: Revision marker for the prebinding-facing projection of that public input. It is
#: separate from PROMPT_REVISION and CONTRACT_REVISION on purpose: a run prepared
#: without --public-context-json produces byte-identical prompt and contract files,
#: so the existing recorded runs stay comparable, while a run that carries public
#: input is identifiable by this marker.
#: ``_v1_1`` corrects one wording hazard only. The earlier text said the speaker's
#: landmark "is not a target and not a candidate", which reads as a prohibition and
#: would make a correct target be excluded whenever the instruction happens to name
#: the object the speaker stands near. The rule intended, and now stated, is that the
#: anchor on its own never identifies a target.
PUBLIC_CONTEXT_PROMPT_REVISION = "prebinding_public_task_input_v1_1_20261106"
#: Keys of the projection that is actually placed in the prebinding message. The
#: validated ``model_input`` is deliberately excluded: it repeats the whole scene
#: graph, and the prebinding request already carries the graph index.
PREBINDING_PUBLIC_CONTEXT_KEYS = ("public_task_input_revision",
                                  "collection_public_context_version",
                                  "transport_channel_for_the_downstream_worker",
                                  "conversation_context", "public_spatial_context",
                                  "source_graph_attestation", "dialogue_history_turn_count",
                                  "author_supplied_scene_element_ids_present",
                                  "author_supplied_scene_element_id_semantics",
                                  "dialogue_history_absence_semantics",
                                  "user_location_is_not_the_robot_location",
                                  "contains_prebound_roles", "contains_candidate_lists",
                                  "contains_reference_answer", "contains_route")


def prebinding_public_task_context(public_context):
    """Project validated public task input into the block the prebinding message carries.

    ``None`` in, ``None`` out, which is what every existing caller gets.

    This is a projection, not a second validation: :func:`public_context_from_file`
    has already run the worker's own validator, including the anchor checks against
    the complete source graph. Only the dialogue, the public spatial statements and
    the attestation travel; ``model_input`` does not, because it repeats the scene
    graph the index already carries.

    The block is honest about what it does contain. The speaker's
    ``near_scene_element_id`` is a scene element id and it is reported as present
    rather than denied: it names the landmark the author said the speaker stands
    near. It is public input, it is not a target, it is not a candidate list and it
    is not the robot's location.
    """
    if public_context is None:
        return None
    if not isinstance(public_context, dict):
        raise ValueError("Public task input must be an object")
    context = public_context.get("conversation_context")
    spatial = public_context.get("public_spatial_context")
    if not isinstance(context, dict) or not isinstance(spatial, dict):
        raise ValueError("Public task input must carry the validated conversation and "
                         "spatial context")
    attestation = public_context.get(handoff_module.PUBLIC_CONTEXT_ATTESTATION_KEY)
    history = context.get("dialogue_history", [])
    if not isinstance(history, list):
        raise ValueError("Public dialogue history must be a list")
    location = context.get("user_location")
    anchor = location.get("near_scene_element_id") if isinstance(location, dict) else None
    block = {
        "public_task_input_revision": PUBLIC_CONTEXT_PROMPT_REVISION,
        "collection_public_context_version":
            public_context.get("collection_public_context_version"),
        "transport_channel_for_the_downstream_worker": PUBLIC_CONTEXT_CHANNEL,
        "conversation_context": copy.deepcopy(context),
        "public_spatial_context": copy.deepcopy(spatial),
        "source_graph_attestation": copy.deepcopy(attestation),
        "dialogue_history_turn_count": len(history),
        "author_supplied_scene_element_ids_present": anchor is not None,
        "author_supplied_scene_element_id_semantics":
            "near_scene_element_id names the landmark the speaker stands near. It is the "
            "author's public input and it is not the robot's location. On its own it never "
            "identifies a target: do not pick a role's object because the speaker is near "
            "it. It is equally not a ban. When the instruction independently refers to that "
            "same object, the object is an ordinary candidate and this id may be the answer; "
            "never exclude an otherwise correct object only because it coincides with the "
            "speaker's anchor.",
        "dialogue_history_absence_semantics":
            "An absent or empty dialogue history means the record carries no authored user "
            "speech. Nothing was removed, shortened or rewritten.",
        "user_location_is_not_the_robot_location": True,
        "contains_prebound_roles": False,
        "contains_candidate_lists": False,
        "contains_reference_answer": False,
        "contains_route": False,
    }
    if set(block) != set(PREBINDING_PUBLIC_CONTEXT_KEYS):
        raise ValueError("The prebinding public-context projection changed shape")
    return block


def public_context_utterances(block):
    """The authored utterances the prebinding message must literally contain.

    Used by the paid runner to check delivery rather than assume it.
    """
    if block is None:
        return []
    history = block.get("conversation_context", {}).get("dialogue_history", [])
    return [turn["text"] for turn in history
            if isinstance(turn, dict) and isinstance(turn.get("text"), str)]


def public_context_from_file(path, instruction, scene_id, nodes, full_graph_sha256):
    """Validate an opt-in public context file with the worker's own validator.

    Returns the block the handoff task will carry, or raises. No second allowlist is
    written here on purpose: a copy would drift away from the channel that actually
    transports the text to the three model requests.

    The node-level checks - the user-location anchor exists, is active and matches
    its recorded room annotation - run here, against the complete source graph, and
    the result is carried as an attestation. They cannot run later: this filter
    crops the graph to a route, so the object the speaker stands near is often not
    in the subgraph the worker receives. The alternative repairs are both refused.
    Dropping the dialogue would discard public task input, and moving the stated
    user room to a room the crop happens to keep would invent a fact. The robot
    location stays unknown either way; a user location is never a robot location.
    """
    from ..data_collection import context_contract
    document = _read(path)
    if not isinstance(document, dict) or set(document) != set(PUBLIC_CONTEXT_FILE_KEYS):
        raise ValueError("A public context file must carry exactly: "
                         + ", ".join(PUBLIC_CONTEXT_FILE_KEYS))
    if document["instruction"] != instruction:
        raise ValueError("The public context file instruction differs from --instruction; "
                         "the user's sentence is never rewritten to fit a pipeline")
    probe = {"instruction": instruction, "scene_id": scene_id,
             "collection_public_context_version": context_contract.VERSION,
             "model_input": document["model_input"],
             "conversation_context": document["conversation_context"],
             "public_spatial_context": document["public_spatial_context"]}
    # Raises on any unexpected or invented field, including an unknown spatial key,
    # and on an anchor that is missing, inactive or annotated with another room.
    attestation = context_contract.source_graph_attestation(probe, nodes, full_graph_sha256)
    return {"collection_public_context_version": context_contract.CROPPED_VERSION,
            "model_input": document["model_input"],
            "conversation_context": document["conversation_context"],
            "public_spatial_context": document["public_spatial_context"],
            context_contract.ATTESTATION_KEY: attestation}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _scene_inputs(scene_dir):
    scene_dir = Path(scene_dir)
    nodes = _read(scene_dir / SCENE_GRAPH)
    edges = _read(scene_dir / SCENE_EDGES)
    metadata_path = scene_dir / SCENE_METADATA
    if not metadata_path.is_file():
        raise ValueError("Cropped-scope filtering requires scene_metadata.json in " + str(scene_dir))
    metadata = _read(metadata_path)
    if not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(metadata, dict):
        raise ValueError("Expected a node array, an edge array and a metadata object")
    return nodes, edges, metadata


def source_digests(scene_dir, overlay_path):
    scene_dir = Path(scene_dir)
    digests = {name: stage_module.sha256_file(scene_dir / name) for name in SCENE_FILES}
    digests["overlay"] = stage_module.sha256_file(overlay_path)
    return digests


def verify_prepared_inputs(run_dir):
    """Re-read prepare's record and confirm every mutable input is byte-identical.

    Returns ``(config, scene_inputs, overlay, overlay_report, recorded_hashes)``.
    """
    run_dir = Path(run_dir)
    stage_module.require_stage(run_dir, "prepare")
    config = _read(run_dir / "prepare/config.json")
    recorded = _read(run_dir / "prepare/input_hashes.json")
    actual = source_digests(config["scene_dir"], config["overlay"])
    changed = sorted(name for name in actual if actual[name] != recorded["source_sha256"].get(name))
    if changed:
        raise ValueError("Input changed after prepare: " + ", ".join(
            name + " recorded=" + str(recorded["source_sha256"].get(name)) + " actual="
            + actual[name] for name in changed))
    if binding_module.instruction_sha256(config["instruction"]) != config["instruction_sha256"]:
        raise ValueError("Locked instruction does not match its recorded digest")
    nodes, edges, metadata = _scene_inputs(config["scene_dir"])
    if metadata.get("scene_id") != config["scene_id"]:
        raise ValueError("Scene metadata scene_id changed after prepare")
    loaded, report = overlay_module.load_overlay(config["overlay"], nodes=nodes,
                                                 scene_id=config["scene_id"])
    overlay_module.verify_source_hashes(loaded, Path(config["scene_dir"]) / SCENE_GRAPH,
                                        Path(config["scene_dir"]) / SCENE_EDGES)
    overlay_module.verify_coordinate_frame(loaded, metadata)
    return config, (nodes, edges, metadata), loaded, report, recorded


#: One row per outcome, listing every field that moves with it. The two refused
#: responses that motivated this revision each wrote one half of a row - a status
#: from one row with the candidate list and stop flags of another - so the rows are
#: stated together rather than left to be assembled from prose rules.
FINAL_STATE_TABLE = (
    {"outcome": "ready_for_structural_route",
     "when": "every role is resolved to exactly one object AND the index records a region for "
             "each of those objects. If even one of them has no recorded region, this is not "
             "the row - use 'unique object, room not recorded' instead",
     "meaning": "The document is complete enough for the filter to compute a structural route "
                "over the recorded regions. It is not a claim that the task is physically "
                "executable, reachable or safe; nothing here was measured on a robot",
     "status": "bound", "object_id": "integer", "candidate_object_ids": "[]",
     "role_clarification_question": "null",
     "region_id": "the region_id the index records for that object; stating it is optional, but "
                  "in this row the index does record one",
     "stop_execution": False, "stop_reason": "null", "needs_environment_context": False,
     "visit_role_ids": "the ordered visits, revisits repeated",
     "terminal_role_id": "the last visit", "terminal_robot_location_required": "explicit boolean",
     "document_clarification_question": "null", "missing_information": "[]"},
    {"outcome": "instance ambiguity",
     "when": "two or more different objects could be the one meant",
     "status": "ambiguous", "object_id": "null",
     "candidate_object_ids": "at least two distinct integers",
     "role_clarification_question": "required, by visible properties, never by id",
     "region_id": "null",
     "stop_execution": True, "stop_reason": "clarification_required",
     "needs_environment_context": False, "visit_role_ids": "[]",
     "terminal_role_id": "null", "terminal_robot_location_required": False,
     "document_clarification_question": "optional", "missing_information": "optional"},
    {"outcome": "unique object, room not recorded",
     "when": "exactly one object fits, and the index records no region for it",
     "status": "bound", "object_id": "integer", "candidate_object_ids": "[]",
     "role_clarification_question": "null", "region_id": "null",
     "stop_execution": True, "stop_reason": "needs_environment_context",
     "needs_environment_context": True, "visit_role_ids": "[]",
     "terminal_role_id": "null", "terminal_robot_location_required": False,
     "document_clarification_question": "required, asking which room",
     "missing_information": "one entry, kind 'environment'"},
    {"outcome": "object not in the index",
     "when": "the instruction needs an object the index does not contain",
     "status": "missing_environment_information", "object_id": "null",
     "candidate_object_ids": "[]", "role_clarification_question": "null", "region_id": "null",
     "stop_execution": True, "stop_reason": "missing_environment_information",
     "needs_environment_context": True, "visit_role_ids": "[]",
     "terminal_role_id": "null", "terminal_robot_location_required": False,
     "document_clarification_question": "optional",
     "missing_information": "one entry, kind 'environment'"},
    {"outcome": "the user has not said enough",
     "when": "the instruction itself leaves the role underspecified",
     "status": "missing_user_information", "object_id": "null",
     "candidate_object_ids": "[]",
     "role_clarification_question": "required, concrete", "region_id": "null",
     "stop_execution": True, "stop_reason": "missing_user_information",
     "needs_environment_context": False, "visit_role_ids": "[]",
     "terminal_role_id": "null", "terminal_robot_location_required": False,
     "document_clarification_question": "optional",
     "missing_information": "one entry, kind 'user'"},
    {"outcome": "task shape this version cannot handle",
     "when": "the instruction is not ordered navigation visits",
     "status": "whichever status is accurate for each role",
     "roles": "Each role keeps the fields of its own status exactly as the rows above require: "
              "a bound role carries an integer object_id and no question, an ambiguous role "
              "carries at least two distinct candidate ids and its own question, a "
              "missing_user_information role carries its own question. This row changes the "
              "document-level stop fields only",
     "object_id": "per that role's status row",
     "candidate_object_ids": "per that role's status row",
     "role_clarification_question": "per that role's status row",
     "region_id": "per that role's status row",
     "stop_execution": True, "stop_reason": "unsupported_task_shape",
     "needs_environment_context": False, "visit_role_ids": "[]",
     "terminal_role_id": "null", "terminal_robot_location_required": False,
     "document_clarification_question": "null",
     "missing_information": "[] - a capability limit is never missing user information, and "
                            "stop_detail is required"},
)
#: Checked against the finished document, field by field, before it is returned.
CROSS_FIELD_SELF_CHECK = (
    "Find the one row of final_state_table that describes each role, then read your own "
    "document against that row field by field. Do not mix fields from two rows.",
    "If writing evidence or a question changed your mind, go back and change status, object_id "
    "and candidate_object_ids to match before you answer. The structured fields are the answer; "
    "the prose is not read as one. The filter checks what it can decide - field types and "
    "cardinality, which fields must move together, whether a declared region_id matches the "
    "index, and whether a room the instruction named is contradicted. It does not interpret "
    "free text, so prose that contradicts your own fields is not caught and corrected for you: "
    "it simply ships a wrong answer under a well-formed shape. Synchronising them is your job.",
    "status=ambiguous requires at least two distinct candidate ids. If you have one, the object "
    "is identified: either bind it, or say what else is missing with the status for that.",
    "object_id is null unless status is bound. candidate_object_ids is empty unless status is "
    "ambiguous.",
    "stop_execution is true whenever any role is not bound, and a stopped document carries no "
    "visit order, no terminal role and no partial plan.",
    "region_id is either exactly what the index records for that object or null. Never a room "
    "you reasoned your way to.",
    "Being unable to produce an executable plan is a correct outcome. Never dissolve a real "
    "ambiguity, and never drop a restriction the instruction states, in order to produce one.",
)


def prebinding_prompt(instruction, start, initial_state, index_filename, contract_filename,
                      public_task_context=None):
    """Operator-facing prompt text. Rendering it makes no request.

    ``public_task_context`` is the opt-in projection from
    :func:`prebinding_public_task_context`. With ``None`` the rendered text is
    byte-identical to the text every earlier run was prepared with. With a block,
    the authored dialogue and the public spatial statements are placed in the prompt
    itself, so the prebinding request sees the same public input the downstream
    worker receives. Storing it in ``config.json`` alone would leave this request
    blind to it and would make the comparison between the two arms meaningless.
    """
    sections = [
        "TASK: resolve every role referenced by the user instruction to scene graph object ids.",
        "PROMPT_REVISION: " + PROMPT_REVISION,
        "",
        "INSTRUCTION (verbatim, never rewrite it):",
        instruction,
        "",
        "DECLARED SCENARIO START (an assumption supplied with the task, not an observation):",
        json.dumps(start, ensure_ascii=False, sort_keys=True),
        "",
        "DECLARED INITIAL STATE (empty object means nothing was declared):",
        json.dumps(initial_state, ensure_ascii=False, sort_keys=True),
        "",
    ]
    if public_task_context is not None:
        sections += [
            "PUBLIC TASK INPUT (" + PUBLIC_CONTEXT_PROMPT_REVISION + "; authored collection",
            "input supplied with this task, carried here verbatim. It is public input, not an",
            "answer: it contains no prebound role, no candidate list and no route. The same",
            "block is attached in full inside " + contract_filename + " under",
            "public_task_context):",
            json.dumps(public_task_context, ensure_ascii=False, sort_keys=True),
            "",
            "HOW TO USE THE PUBLIC TASK INPUT",
            "P1. The dialogue history is user speech, not executed robot actions, and not a",
            "    record of anything this pipeline did.",
            "P2. Use the stated user location for 'here' and 'me'. Keep the explicit referents",
            "    from the dialogue for 'there' and 'that item'. Never resolve 'there' to the",
            "    speaker's position.",
            "P3. The user location is not the robot location. The robot location is unknown and",
            "    unmeasured; the only location you may assume is the declared scenario start.",
            "P4. near_scene_element_id names the landmark the speaker stands near. On its own",
            "    it never identifies a target, so do not bind a role to it because the speaker",
            "    is near it. This is not a ban either: if the instruction independently refers",
            "    to that same object, it is an ordinary candidate and its id may be the answer.",
            "    Never rule out an object only because it is the speaker's anchor.",
            "P5. An empty dialogue history means the author supplied no dialogue. It does not",
            "    mean anything was removed, and it is not a reason to invent a referent.",
            "",
        ]
    sections += [
        "ATTACHED FILES (their full contents must be supplied to the model together with this",
        "prompt; a file name alone is not an input):",
        "  - " + index_filename + ": the complete graph index, every node and every supplied edge",
        "  - " + contract_filename + ": the required output contract",
        "",
        "RULES",
        "1. " + binding_module.prebinding_contract_text(),
        "2. Use role_id values role_001, role_002, ... in the order the roles are declared. That",
        "   declaration order is not the order of actions.",
        "3. role_text must be copied as an exact substring of the instruction above.",
        "4. The validated task shape of this filter version is ordered navigation visits. If the",
        "   instruction needs a shape this version does not handle, stop with",
        "   stop_reason=\"unsupported_task_shape\" and a concrete stop_detail. That is a filter",
        "   capability limit: never report it as missing user information.",
        "5. Bind a role only when exactly one object fits. ambiguous means two or more *different",
        "   objects* could be the one the user meant, so it always carries at least two distinct",
        "   candidate_object_ids. When a role is ambiguous: status=ambiguous, object_id=null, at",
        "   least two distinct candidate_object_ids, that role's own concrete",
        "   clarification_question, stop_execution=true, visit_role_ids=[], and plan nothing. Do",
        "   not execute any prefix of the task. Never report ambiguous with a single candidate:",
        "   one candidate means the object is identified, and whatever else is missing is not an",
        "   instance ambiguity.",
        "6. Never invent an object id. If the instruction needs an object the index does not",
        "   contain, use missing_environment_information; if the user has not said enough, use",
        "   missing_user_information with a concrete question.",
        "7. Report the ordered visit sequence in visit_role_ids. Keep a revisit as a repeated",
        "   entry; do not collapse it. terminal_role_id is the last entry.",
        "   Set terminal_robot_location_required explicitly: true only when the task really",
        "   requires the robot to end up at that target. It is not implied by the last visit.",
        "8. Region annotation in the index is a source-asset geometry index, not a verified map.",
        "   An unknown room is NOT an instance ambiguity. If exactly one object fits the role but",
        "   its region_status is 'unknown', the role stays status=bound and keeps that object_id:",
        "   you know which object is meant, you only do not know which room it is in. Set",
        "   region_id=null, candidate_object_ids=[], that role's clarification_question=null,",
        "   needs_environment_context=true, stop_execution=true, visit_role_ids=[],",
        "   terminal_role_id=null, terminal_robot_location_required=false, and put the question",
        "   about the room in the document-level clarification_question together with a",
        "   missing_information entry of kind 'environment'. Do not guess the room and do not",
        "   substitute the declared start region: a missing room cannot be recovered from the",
        "   graph, so stopping is the correct outcome.",
        "9. duplicate_label_groups lists objects that share a label. A shared label alone is not",
        "   ambiguity: first try to separate the candidates with caption, attributes, relations",
        "   and the task context. Ask only when more than one candidate still fits after that,",
        "   and never resolve it by picking the lowest id. Two objects with *different* labels",
        "   can still compete when their captions describe the same thing in the same words;",
        "   treat that as ambiguity too.",
        "10. A clarification_question must be answerable by a person who is standing in the room",
        "    and cannot see the scene graph. Quote the distinguishing properties that actually",
        "    differ between the candidates - shelf count, frame or enclosure, colour, material,",
        "    size, what it stands on or next to - taken from their own captions, and name the",
        "    room when the candidates are in different rooms. Never ask the user to choose by",
        "    object id, index position or any internal identifier, and never offer ids as the",
        "    options: the ids are yours, not theirs. If the captions genuinely contain nothing",
        "    that separates the candidates, say exactly that in the question and ask for the",
        "    property you would need.",
        "11. Only active objects may be bound or offered as candidates.",
        "12. When the instruction itself names the room - \"the sign holder in the hallway\" -",
        "    that room is part of what the user asked for. region_qualifier_constraints in the",
        "    contract file states which room names are recorded, which wordings this filter",
        "    recognises, and what a room constraint does and does not exclude. Read its rules.",
        "    Two things in particular: an object the index records in a different room is not a",
        "    competing candidate, and an object with no recorded room cannot be excluded by a",
        "    room name and must not be assigned one. If the filter recognises no constraint for",
        "    a wording, that is a limit of its grammar and not evidence that information is",
        "    missing: the restriction the instruction states still applies to you, so reason it",
        "    out from the index yourself and bind when the recorded facts show exactly one",
        "    object satisfies it. Stop and report only when the facts are genuinely absent or do",
        "    not distinguish the candidates.",
        "13. roles[].evidence is optional. Omit it, or write one short sentence naming the",
        "    index fields you used. Do not write a long justification; its length is not",
        "    checked and it never decides acceptance. The decision lives only in status,",
        "    object_id, candidate_object_ids and region_id. If you conclude something different",
        "    while thinking, change those fields before you answer.",
        "14. Output a single JSON object that validates against output_json_schema in the",
        "    contract file. No prose. output_example shows value types only; its values are",
        "    synthetic illustration values; every id in an answer must come from the attached",
        "    index. Allowed values appear in the schema enum lists, so never copy an enum list",
        "    into a value: role_kind and status are single strings, stop_reason is a single",
        "    string or null, object_id is an integer or null.",
        "",
        "FINAL STATE TABLE (one row per outcome; take every field of a role from one row and",
        "never mix two rows). The first row is named ready_for_structural_route because it only",
        "means the filter can compute a structural route over recorded regions; it is not a",
        "claim that the task is physically executable. The same table is in the contract file",
        "under final_state_table:",
        json.dumps(FINAL_STATE_TABLE, ensure_ascii=False, indent=2, sort_keys=True),
        "",
        "BEFORE YOU ANSWER, check your own document against this list:",
    ]
    sections += ["  - " + item for item in CROSS_FIELD_SELF_CHECK]
    return "\n".join(sections) + "\n"


#: Synthetic ids used in the contract's examples, so that every example satisfies the
#: type and cardinality it illustrates. They are illustration only and are not
#: reserved or guaranteed-invalid values: existence is enforced by checking an id
#: against the attached index, which is what refuses an id that is not there.
PLACEHOLDER_OBJECT_IDS = (90001, 90002)


def prebinding_request_contract(scene_id, instruction, region_constraints=None,
                                public_task_context=None):
    """Typed output contract: a JSON Schema for types, plus a correctly typed example.

    Allowed values live in the schema ``enum`` lists only. An earlier version placed
    ``list(ROLE_KINDS)`` and ``list(ROLE_STATUSES)`` directly in the example, which
    reads as "role_kind is an array of kinds" and produced ``role_kind:
    ["visit_target"]``. Enumerations are never shown in value position here.

    Every example value also obeys its own declared type and cardinality. The
    earlier placeholders did not: an ambiguous example carried a one-element array
    of a string where the rules demand at least two distinct integers, and a bound
    example carried a string where an integer is required. A contract that
    contradicts itself in its examples is an invitation to fill the fields
    inconsistently, so the placeholders are synthetic integers instead of prose.

    ``region_constraints`` is the program-derived room-constraint block from
    :mod:`region_phrase`. It carries rules, the recorded room-name vocabulary and
    a lexical scan of the instruction; it carries no object id, no candidate list
    and no answer.

    ``public_task_context`` is the opt-in public collection input projected by
    :func:`prebinding_public_task_context`. The key is only added when a block was
    supplied, so a run prepared without public input produces the same contract
    document as before. The runner attaches this file in full, which is how the
    block actually reaches the model rather than only the run directory.
    """
    role_properties = {
        "role_id": {"type": "string", "pattern": "^role_[0-9]{3,}$"},
        "role_kind": {"type": "string", "enum": list(binding_module.ROLE_KINDS),
                      "description": "exactly one kind as a plain string, never an array"},
        "role_text": {"type": "string",
                      "description": "exact substring of the instruction"},
        "expected_labels": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "status": {"type": "string", "enum": list(binding_module.ROLE_STATUSES),
                   "description": "exactly one status as a plain string, never an array"},
        "object_id": {"type": ["integer", "null"],
                      "description": "integer when status is bound, otherwise null"},
        "candidate_object_ids": {"type": "array", "items": {"type": "integer"},
                                 "uniqueItems": True,
                                 "description": "at least two distinct ids when status is "
                                                "ambiguous, otherwise an empty array"},
        "region_id": {"type": ["string", "null"],
                      "description": "optional; when given it must equal the region_id the "
                                     "index records for this object. Null is required when the "
                                     "index records no room for it - never state a room the "
                                     "index does not"},
        "clarification_question": {"type": ["string", "null"],
                                   "maxLength": binding_module.CLARIFICATION_QUESTION_MAX_CHARS,
                                   "description": "required for ambiguous and "
                                                  "missing_user_information, otherwise null; a "
                                                  "question for a person, not a derivation"},
        "evidence": {"type": ["string", "null"],
                     "description": "optional; omit it, or one short sentence naming the "
                                    "index fields used. Its length is not checked and it "
                                    "never decides acceptance; the fields above carry the "
                                    "decision"},
    }
    document = {
        "binding_version": binding_module.BINDING_VERSION,
        "contract_revision": CONTRACT_REVISION,
        "prompt_revision": PROMPT_REVISION,
        "revision_note": "Types and allowed values are declared in output_json_schema. This "
                         "revision makes roles[].evidence an optional short sentence with no "
                         "length limit (it is kept in a separate audit log, not in the "
                         "engineering JSON), keeps the final-state table, keeps the character "
                         "bound on "
                         "clarification_question, states that a declared region_id is "
                         "re-checked against the index, and keeps the example placeholders "
                         "obeying their own declared types. binding_version is unchanged: the "
                         "accepted document shape is the same.",
        "field_budgets": {
            "roles[].evidence": {"optional": True, "length_limit_enforced": False,
                                 "wanted": "omit, or one short sentence"},
            "roles[].clarification_question": {
                "maximum_characters": binding_module.CLARIFICATION_QUESTION_MAX_CHARS,
                "measured_in": "characters"},
            "why": "The decision lives in status, object_id, candidate_object_ids and "
                   "region_id, never in prose. A question is for a person, so it stays short.",
        },
        "final_state_table": FINAL_STATE_TABLE,
        "cross_field_self_check": CROSS_FIELD_SELF_CHECK,
        "region_qualifier_constraints": region_constraints,
        "task_class": binding_module.SUPPORTED_TASK_CLASS,
        "output_json_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "topology_prebinding_v1_output",
            "type": "object",
            "additionalProperties": False,
            "required": ["binding_version", "task_class", "scene_id", "instruction",
                         "instruction_sha256", "role_order_is_not_action_order", "roles",
                         "visit_role_ids", "terminal_role_id",
                         "terminal_robot_location_required", "stop_execution"],
            "properties": {
                "binding_version": {"const": binding_module.BINDING_VERSION},
                "task_class": {"const": binding_module.SUPPORTED_TASK_CLASS},
                "scene_id": {"type": "string"},
                "instruction": {"const": instruction},
                "instruction_sha256": {
                    "const": binding_module.instruction_sha256(instruction)},
                "role_order_is_not_action_order": {"const": True},
                "roles": {"type": "array", "minItems": 1, "items": {
                    "type": "object",
                    "required": ["role_id", "role_kind", "role_text", "expected_labels",
                                 "status"],
                    "properties": role_properties}},
                "visit_role_ids": {"type": "array", "items": {"type": "string"},
                                   "description": "ordered visit targets; a revisit is a "
                                                  "repeated entry; empty when stopping"},
                "terminal_role_id": {"type": ["string", "null"]},
                "terminal_robot_location_required": {"type": "boolean"},
                "stop_execution": {"type": "boolean"},
                "stop_reason": {"type": ["string", "null"],
                                "enum": list(binding_module.STOP_REASONS) + [None]},
                "stop_detail": {"type": ["string", "null"],
                                "description": "required when stop_reason is "
                                               "unsupported_task_shape"},
                "needs_environment_context": {"type": "boolean"},
                "clarification_question": {"type": ["string", "null"]},
                "missing_information": {"type": "array", "items": {
                    "type": "object",
                    "required": ["kind", "detail"],
                    "properties": {
                        "role_id": {"type": ["string", "null"]},
                        "kind": {"type": "string", "enum": ["environment", "user"]},
                        "detail": {"type": "string"}}}},
            },
        },
        "output_example": {
            "binding_version": binding_module.BINDING_VERSION,
            "task_class": binding_module.SUPPORTED_TASK_CLASS,
            "scene_id": scene_id,
            "instruction": instruction,
            "instruction_sha256": binding_module.instruction_sha256(instruction),
            "role_order_is_not_action_order": True,
            "roles": [{
                "role_id": "role_001",
                "role_kind": "visit_target",
                "role_text": "exact substring of the instruction",
                "expected_labels": ["label", "synonym"],
                "status": "bound",
                "object_id": PLACEHOLDER_OBJECT_IDS[0],
                "candidate_object_ids": [],
                "region_id": None,
                "clarification_question": None,
            }],
            "visit_role_ids": ["role_001"],
            "terminal_role_id": "role_001",
            "terminal_robot_location_required": True,
            "stop_execution": False,
            "stop_reason": None,
            "stop_detail": None,
            "needs_environment_context": False,
            "clarification_question": None,
            "missing_information": [],
        },
        "example_semantics": "output_example shows value types and shapes only. Its values are "
                             "placeholders, not an answer; the object_id is a synthetic "
                             "placeholder integer and must be replaced by a real index id.",
        "placeholder_policy": {
            "object_ids": list(PLACEHOLDER_OBJECT_IDS),
            "statement": "Every id in every example on this contract is a synthetic illustration "
                         "value, there to show the type and the cardinality of each field. They "
                         "are not reserved ids and no claim is made about which ids any "
                         "particular graph uses. Never copy one into an answer: an answer id is "
                         "accepted only if the attached index contains it.",
            "cardinality": "Each example also satisfies the rules it illustrates - an ambiguous "
                           "role shows two distinct integer candidate ids, a bound role shows "
                           "one integer object_id - so the examples never contradict the rules.",
        },
        "no_answer_examples": {
            "semantics": "Two symbolic stop shapes, written with synthetic placeholder ids and "
                         "ROLE_LABEL words instead of real values. They show which fields move "
                         "together when nothing can be executed. They are not answers to this "
                         "task; ROLE_LABEL is not a label to copy and the ids are not real.",
            "instance_ambiguity_two_or_more_objects_fit": {
                "when": "Two or more different objects could be the one the user meant. You do "
                        "not know WHICH object.",
                "roles": [{
                    "role_id": "role_001", "role_kind": "visit_target",
                    "role_text": "exact substring of the instruction",
                    "expected_labels": ["ROLE_LABEL"],
                    "status": "ambiguous", "object_id": None,
                    "candidate_object_ids": list(PLACEHOLDER_OBJECT_IDS),
                    "region_id": None,
                    "clarification_question": "a question that names the properties which differ "
                                              "between the candidates, never their ids",
                }],
                "document_fields": {
                    "stop_execution": True, "stop_reason": "clarification_required",
                    "needs_environment_context": False, "visit_role_ids": [],
                    "terminal_role_id": None, "terminal_robot_location_required": False},
                "do_not": "Do not report this shape with a single candidate id. One candidate "
                          "means the object is identified, so whatever is missing is not an "
                          "instance ambiguity.",
            },
            "unique_object_with_unknown_room": {
                "when": "Exactly one object fits, so you DO know which object; its "
                        "region_status is 'unknown', so you do not know WHERE it is. This is "
                        "missing environment information, not an ambiguity.",
                "roles": [{
                    "role_id": "role_002", "role_kind": "visit_target",
                    "role_text": "exact substring of the instruction",
                    "expected_labels": ["ROLE_LABEL"],
                    "status": "bound", "object_id": PLACEHOLDER_OBJECT_IDS[0],
                    "candidate_object_ids": [], "region_id": None,
                    "clarification_question": None,
                }],
                "document_fields": {
                    "stop_execution": True, "stop_reason": "needs_environment_context",
                    "needs_environment_context": True, "visit_role_ids": [],
                    "terminal_role_id": None, "terminal_robot_location_required": False,
                    "clarification_question": "which room the identified object is in",
                    "missing_information": [{"role_id": "role_002", "kind": "environment",
                                             "detail": "the object is identified and stays "
                                                       "bound; its room is not recorded"}]},
                "do_not": "Do not downgrade the role to ambiguous with one candidate, do not set "
                          "object_id to null, and do not guess a room from the declared start.",
            },
        },
        "scalar_field_reminders": [
            "role_kind is a single string from the schema enum, never an array.",
            "status is a single string from the schema enum, never an array.",
            "stop_reason is a single string or null, never an array.",
            "object_id is an integer or null, never a string and never an array.",
            "region_id is a string or null.",
            "missing_information[].kind is the string 'environment' or 'user'.",
        ],
        "hard_rules": [
            "role_id must match ^role_[0-9]{3,}$ and must be unique.",
            "role_text must be an exact substring of the instruction.",
            "An ambiguous or missing_user_information role needs its own concrete question.",
            "A stopped binding carries no visit order and no partial plan.",
            "A shared label alone is not ambiguity; discriminating evidence must be tried first.",
            "ambiguous means two or more different objects fit and requires at least two "
            "distinct candidate_object_ids. A single candidate is never an ambiguity.",
            "An object that is uniquely identified but has region_status 'unknown' stays bound "
            "and keeps its object_id; the run stops with needs_environment_context=true. Not "
            "knowing which room is not the same as not knowing which object.",
            "A clarification question must separate the candidates by properties a person can "
            "see, taken from their own captions, and must never ask the user to choose by "
            "object id or any other internal identifier.",
            "An unsupported task shape uses stop_reason=unsupported_task_shape, never "
            "missing_user_information.",
            "An unknown region never becomes a guessed room.",
            "An object explicitly marked inactive may not be bound or offered as a candidate; a "
            "graph without an active flag keeps the existing assumed-active convention.",
            "terminal_robot_location_required must be an explicit boolean.",
            "Every field keeps the type declared in output_json_schema. A single-element "
            "array in place of a string, such as role_kind [\"visit_target\"], is rejected.",
            "roles[].evidence is optional and not length-bounded: omit it or write one short "
            "sentence. It never decides acceptance. If you decide the answer is different, "
            "change status, object_id and candidate_object_ids; only those fields carry the "
            "decision.",
            "A declared region_id must equal the region_id the index records for that object, "
            "and must be null when the index records none. It is re-checked, not trusted.",
            "When a role_text names a room, the bound object must be the one the index records "
            "in that room, and objects the index records in a different room are not competing "
            "candidates. An object with no recorded room cannot be excluded by a room name.",
            "Producing an executable document is never a reason to resolve a real ambiguity. An "
            "unrecognised wording neither cancels a restriction the instruction states nor "
            "proves that information is missing: decide it from the recorded facts, and stop "
            "only when those facts are absent or do not distinguish the candidates.",
        ],
    }
    if public_task_context is not None:
        document["public_task_context"] = public_task_context
        document["public_task_context_semantics"] = (
            "Authored public collection input supplied with this task and carried here "
            "verbatim, so this request receives the same public input the downstream worker "
            "receives. It is input, not an answer: it holds no prebound role, no candidate "
            "list, no route and no reference answer. The dialogue is user speech, not "
            "executed robot actions. The stated user location is not the robot location, and "
            "near_scene_element_id names the landmark the speaker stands near, not a target.")
    return document


def cmd_prepare(args):
    stage_dir = stage_module.open_stage(args.run_dir, "prepare", fresh_run=True)
    try:
        if args.task_class != binding_module.SUPPORTED_TASK_CLASS:
            raise ValueError("Only " + binding_module.SUPPORTED_TASK_CLASS
                             + " is implemented in this version")
        nodes, edges, metadata = _scene_inputs(args.scene_dir)
        loaded, report = overlay_module.load_overlay(args.overlay, nodes=nodes,
                                                     scene_id=metadata.get("scene_id"))
        hash_check = overlay_module.verify_source_hashes(
            loaded, Path(args.scene_dir) / SCENE_GRAPH, Path(args.scene_dir) / SCENE_EDGES)
        frame = overlay_module.verify_coordinate_frame(loaded, metadata)
        if args.start_region_id is not None:
            start_region = args.start_region_id
            if overlay_module.region_by_id(loaded, start_region) is None:
                raise ValueError("Start region is not in the overlay: " + str(start_region))
            start = {"region_id": start_region, "object_id": None,
                     "basis": "scenario_assumption_region_supplied_by_caller"}
        else:
            entry = overlay_module.object_region_entry(loaded, args.start_object_id)
            if entry is None or entry.get("status") != "assigned":
                raise ValueError("Start object has no assigned region; supply --start-region-id "
                                 "explicitly instead of guessing a room")
            start = {"region_id": entry["region_id"], "object_id": args.start_object_id,
                     "basis": "scenario_assumption_from_start_object_region"}
        start["statement"] = ("Declared scenario assumption; not evidence of an actual robot "
                              "location.")
        initial_state = _read(args.initial_state_json) if args.initial_state_json else {}
        if not isinstance(initial_state, dict):
            raise ValueError("Initial state must be a JSON object")
        public_context = None
        if getattr(args, "public_context_json", None):
            public_context = public_context_from_file(
                args.public_context_json, args.instruction, metadata.get("scene_id"), nodes,
                stage_module.sha256_file(Path(args.scene_dir) / SCENE_GRAPH))
        # Projected once and delivered twice: in the prompt text and inside the
        # request contract. Both files are read and sent in full by the paid runner,
        # which also re-checks that the authored utterances are present in the
        # message it is about to send. A block that only exists in config.json would
        # never reach the model.
        public_task_context = prebinding_public_task_context(public_context)
        digests = source_digests(args.scene_dir, args.overlay)
        stage_module.write_new(stage_dir / "lightweight_index.json",
                               index_module.build_index(nodes, edges, loaded))
        stage_module.write_new(stage_dir / "overlay_report.json", report)
        stage_module.write_new(stage_dir / "input_hashes.json", {
            "scene_dir": str(Path(args.scene_dir).resolve()),
            "overlay_path": str(Path(args.overlay).resolve()),
            "source_sha256": digests,
            "full_graph_sha256": digests[SCENE_GRAPH],
            "full_graph_edges_sha256": digests[SCENE_EDGES],
            "full_graph_node_count": len(nodes),
            "full_graph_edge_count": len(edges),
            "overlay_provenance_hash_check": hash_check,
            "coordinate_frame": frame})
        with (stage_dir / "prebinding_prompt.txt").open("x", encoding="utf-8") as stream:
            stream.write(prebinding_prompt(args.instruction, start, initial_state,
                                           "lightweight_index.json",
                                           "prebinding_request_contract.json",
                                           public_task_context))
        # The room-constraint block travels inside the request contract, which the
        # runner attaches in full and hashes; it is not a separate attachment.
        region_constraints = region_phrase_module.region_constraint_block(loaded,
                                                                         args.instruction)
        stage_module.write_new(stage_dir / "prebinding_request_contract.json",
                               prebinding_request_contract(metadata.get("scene_id"),
                                                           args.instruction,
                                                           region_constraints,
                                                           public_task_context))
        stage_module.write_new(stage_dir / "config.json", {
            "pipeline_version": PIPELINE_VERSION,
            "record_id": args.record_id, "scene_id": metadata.get("scene_id"),
            "instruction": args.instruction,
            "instruction_sha256": binding_module.instruction_sha256(args.instruction),
            "scene_dir": str(Path(args.scene_dir).resolve()),
            "overlay": str(Path(args.overlay).resolve()),
            "task_class": args.task_class,
            "start": start, "initial_state": initial_state,
            "input_scope": CROPPED_SCOPE,
            "public_context": public_context,
            "public_context_channel": PUBLIC_CONTEXT_CHANNEL if public_context else None,
            "public_context_is_model_input_not_reference": bool(public_context),
            "prebinding_public_task_context": public_task_context,
            "prebinding_public_context_revision":
                PUBLIC_CONTEXT_PROMPT_REVISION if public_task_context else None,
            "prebinding_public_context_delivered_in":
                ["prebinding_prompt.txt", "prebinding_request_contract.json"]
                if public_task_context else [],
            "prebinding_public_context_delivery_note":
                "The block is rendered into the prompt and attached inside the request "
                "contract, both of which the paid runner sends in full. Storage in this "
                "config file is a record, not a delivery.",
            "expected_model_request_count": {
                "prebinding": handoff_module.PREBINDING_MODEL_REQUESTS,
                "downstream_legacy_stages": handoff_module.DOWNSTREAM_MODEL_REQUESTS,
                "pipeline_total": handoff_module.PREBINDING_MODEL_REQUESTS
                + handoff_module.DOWNSTREAM_MODEL_REQUESTS},
            "actual_model_requests_this_run": 0})
        stage_module.record_artifacts(stage_dir, "prepare", [
            "config.json", "input_hashes.json", "overlay_report.json", "lightweight_index.json",
            "prebinding_prompt.txt", "prebinding_request_contract.json"])
        stage_module.deliver_stage_result(stage_dir, "prepare", {
            "node_count": len(nodes), "edge_count": len(edges),
            "start_region": start["region_id"], "coverage": report["coverage_recomputed"]})
        return 0
    except Exception as exc:
        stage_module.deliver_failure(stage_dir, "prepare", type(exc).__name__, str(exc))
        return 2
    except BaseException as exc:
        stage_module.deliver_failure(stage_dir, "prepare", type(exc).__name__, str(exc),
                                     {"interrupted": True})
        raise


def cmd_ingest_binding(args):
    stage_dir = stage_module.open_stage(args.run_dir, "ingest_binding")
    try:
        config, scene, loaded, _report, _recorded = verify_prepared_inputs(args.run_dir)
        nodes = scene[0]
        digest = stage_module.copy_new(args.from_file, stage_dir / "prebinding_raw.json")
        document = _read(stage_dir / "prebinding_raw.json")
        if isinstance(document, dict) and document.get("scene_id") != config["scene_id"]:
            raise ValueError("Prebinding scene_id " + repr(document.get("scene_id"))
                             + " does not match " + repr(config["scene_id"]))
        review_mode = getattr(args, "candidate_review", review_hooks.MODE_OFF)
        if review_mode != review_hooks.MODE_OFF:
            # Record-only. The frozen raw file and the document handed to the validator
            # are both unchanged: a stopped prebinding carries no visit order and this
            # reviewer does not invent one, so a recovered role is reported as a binding
            # recovery with the stage still stopping exactly as it did before.
            scene_facts = review_hooks.scene_from_inputs(nodes, loaded, scene[1])
            stage_module.write_new(
                stage_dir / "candidate_review.json",
                review_hooks.review_prebinding_document(
                    document, config["instruction"], scene_facts, mode=review_mode))
        summary = binding_module.validate_prebinding(document, config["instruction"],
                                                     overlay=loaded, nodes=nodes,
                                                     task_class=config["task_class"])
        stage_module.write_new(stage_dir / "prebinding_provenance.json", {
            "source": args.source, "source_path": str(Path(args.from_file).resolve()),
            "source_sha256": digest,
            "is_model_output": args.source == "model_response_file",
            "synthetic_offline": args.source != "model_response_file",
            "model_requests_made_by_this_stage": 0,
            "note": "A reviewed offline fixture is not collected data."})
        stage_module.write_new(stage_dir / "prebinding_validated.json", document)
        if summary["stop_execution"]:
            stage_module.deliver_failure(
                stage_dir, "ingest_binding", "prebinding_stopped_before_execution",
                "Prebinding requires clarification or more information; no route, crop or task "
                "was produced.",
                {"unresolved_role_ids": summary["unresolved_role_ids"],
                 "needs_environment_context": bool(document.get("needs_environment_context")),
                 "clarification_question": document.get("clarification_question"),
                 "role_questions": {role["role_id"]: role.get("clarification_question")
                                    for role in document["roles"]
                                    if role.get("clarification_question")},
                 "missing_information": document.get("missing_information")})
            return 3
        stage_module.record_artifacts(stage_dir, "ingest_binding", [
            "prebinding_raw.json", "prebinding_validated.json", "prebinding_provenance.json"])
        stage_module.deliver_stage_result(stage_dir, "ingest_binding", {
            "role_count": len(document["roles"]),
            "visit_role_ids": document["visit_role_ids"]})
        return 0
    except Exception as exc:
        stage_module.deliver_failure(stage_dir, "ingest_binding", type(exc).__name__, str(exc))
        return 2
    except BaseException as exc:
        stage_module.deliver_failure(stage_dir, "ingest_binding", type(exc).__name__, str(exc),
                                     {"interrupted": True})
        raise


def cmd_route(args):
    stage_dir = stage_module.open_stage(args.run_dir, "route")
    try:
        config, _scene, loaded, _report, _recorded = verify_prepared_inputs(args.run_dir)
        stage_module.require_stage(args.run_dir, "ingest_binding")
        document = _read(Path(args.run_dir) / "ingest_binding/prebinding_validated.json")
        visits = binding_module.ordered_visit_objects(document)
        resolved, unresolved = route_module.waypoint_regions(
            loaded, [visit["object_id"] for visit in visits])
        stage_module.write_new(stage_dir / "waypoints.json", {
            "ordered_visits": visits, "resolved": resolved, "unresolved": unresolved})
        if unresolved:
            stage_module.deliver_failure(
                stage_dir, "route", "needs_environment_context",
                "A visit target has no assigned region in the overlay. The room is not guessed; "
                "report missing environment information instead.",
                {"unresolved": unresolved})
            return 3
        for visit, waypoint in zip(visits, resolved):
            waypoint["role_id"] = visit["role_id"]
        plan = route_module.plan_route(loaded, config["start"]["region_id"], resolved)
        stage_module.write_new(stage_dir / "route_plan.json", plan)
        if not plan["complete"]:
            stage_module.deliver_failure(
                stage_dir, "route", "no_structural_connection_in_overlay",
                "At least one leg has no structural connection in the overlay; no route is claimed.",
                {"unreachable_legs": plan["unreachable_legs"],
                 "excluded_closed_portals": plan["excluded_closed_portals"]})
            return 3
        stage_module.record_artifacts(stage_dir, "route", ["waypoints.json", "route_plan.json"])
        stage_module.deliver_stage_result(stage_dir, "route", {
            "total_hop_count": plan["total_hop_count"],
            "regions_visited_in_order": plan["regions_visited_in_order"],
            "distance_semantics": plan["distance_semantics"]})
        return 0
    except Exception as exc:
        stage_module.deliver_failure(stage_dir, "route", type(exc).__name__, str(exc))
        return 2
    except BaseException as exc:
        stage_module.deliver_failure(stage_dir, "route", type(exc).__name__, str(exc),
                                     {"interrupted": True})
        raise


def cmd_slice(args):
    stage_dir = stage_module.open_stage(args.run_dir, "slice")
    try:
        config, scene, loaded, report, recorded = verify_prepared_inputs(args.run_dir)
        stage_module.require_stage(args.run_dir, "ingest_binding")
        stage_module.require_stage(args.run_dir, "route")
        nodes, edges, metadata = scene
        document = _read(Path(args.run_dir) / "ingest_binding/prebinding_validated.json")
        plan = _read(Path(args.run_dir) / "route/route_plan.json")
        if not plan.get("complete"):
            raise ValueError("Route plan is incomplete; refusing to crop")
        task_objects = sorted({role["object_id"] for role in document["roles"]
                               if role.get("object_id") is not None})
        # Optional, off by default: extra ids selected offline by the deterministic
        # context check. Absent flag -> the call and every artifact are unchanged.
        extra_keep = None
        extra_source = getattr(args, "extra_keep_ids_json", None)
        if extra_source:
            selection = _read(Path(extra_source))
            ids = selection.get("extra_keep_ids") if isinstance(selection, dict) else selection
            if not isinstance(ids, list) or not all(
                    isinstance(value, int) and not isinstance(value, bool) for value in ids):
                raise ValueError("--extra-keep-ids-json needs an integer id array, or an object "
                                 "with an integer extra_keep_ids array")
            if not ids:
                raise ValueError("--extra-keep-ids-json was supplied but selects no id; omit the "
                                 "flag instead of requesting an empty expansion")
            extra_keep = sorted(set(ids))
            stage_module.write_new(stage_dir / "context_retrieval_selection.json", {
                "source_file": str(Path(extra_source).resolve()),
                "extra_keep_ids": extra_keep,
                "selection": selection,
                "model_requests_made": 0,
                "note": "Offline id selection only; this stage makes no model request."})
        kept_nodes, kept_edges, audit = slice_module.build_crop(
            nodes, edges, loaded, route_module.route_regions(plan), task_objects,
            extra_keep=extra_keep)
        cropped = stage_dir / "scene"
        cropped.mkdir(exist_ok=False)
        stage_module.write_new(cropped / SCENE_GRAPH, kept_nodes)
        stage_module.write_new(cropped / SCENE_EDGES, kept_edges)
        stage_module.write_new(cropped / SCENE_METADATA, slice_module.crop_metadata(
            metadata, audit, {
                "filter_tool": PIPELINE_VERSION,
                "filter_run_id": Path(args.run_dir).resolve().name,
                "overlay_sha256": report["overlay_sha256"],
                "full_graph_sha256": recorded["full_graph_sha256"],
                "full_graph_edges_sha256": recorded["full_graph_edges_sha256"],
                "full_graph_node_count": recorded["full_graph_node_count"],
                "full_graph_edge_count": recorded["full_graph_edge_count"]}))
        stage_module.write_new(stage_dir / "slice_audit.json", audit)
        artifacts = ["slice_audit.json", "scene/" + SCENE_GRAPH, "scene/" + SCENE_EDGES,
                     "scene/" + SCENE_METADATA]
        if extra_keep is not None:
            artifacts.append("context_retrieval_selection.json")
        stage_module.record_artifacts(stage_dir, "slice", artifacts)
        stage_module.deliver_stage_result(stage_dir, "slice", {
            "retained_node_count": audit["retained_node_count"],
            "dropped_node_count": audit["dropped_node_count"],
            "node_retention_ratio": audit["node_retention_ratio"],
            "unknown_region_objects_retained": len(audit["unknown_region_objects_retained"])})
        return 0
    except Exception as exc:
        stage_module.deliver_failure(stage_dir, "slice", type(exc).__name__, str(exc))
        return 2
    except BaseException as exc:
        stage_module.deliver_failure(stage_dir, "slice", type(exc).__name__, str(exc),
                                     {"interrupted": True})
        raise


def cmd_handoff(args):
    stage_dir = stage_module.open_stage(args.run_dir, "handoff")
    try:
        run_dir = Path(args.run_dir)
        config, _scene, _loaded, _report, recorded = verify_prepared_inputs(run_dir)
        for predecessor in ("ingest_binding", "route", "slice"):
            stage_module.require_stage(run_dir, predecessor)
        document = _read(run_dir / "ingest_binding/prebinding_validated.json")
        provenance = _read(run_dir / "ingest_binding/prebinding_provenance.json")
        plan = _read(run_dir / "route/route_plan.json")
        audit = _read(run_dir / "slice/slice_audit.json")
        overlay_report = _read(run_dir / "prepare/overlay_report.json")
        if document.get("stop_execution"):
            raise ValueError("A stopped prebinding must never be handed off")
        if not plan.get("complete"):
            raise ValueError("An incomplete route must never be handed off")
        scene = run_dir / "slice/scene"
        hashes = {name: stage_module.sha256_file(scene / name)
                  for name in handoff_module.SCENE_FILES}
        context = handoff_module.build_context(
            document, plan, audit, config["start"], config["initial_state"],
            overlay_report, provenance,
            {"full_graph_sha256": recorded["full_graph_sha256"],
             "full_graph_edges_sha256": recorded["full_graph_edges_sha256"],
             "full_graph_node_count": recorded["full_graph_node_count"],
             "full_graph_edge_count": recorded["full_graph_edge_count"]})
        task = handoff_module.build_task(config["record_id"], config["scene_id"],
                                         config["instruction"], hashes, context,
                                         config.get("public_context"))
        if task["instruction"] != config["instruction"]:
            raise ValueError("Instruction must be handed over byte for byte")
        if config.get("public_context") is not None:
            carried = task.get("model_input")
            if not isinstance(carried, dict) or carried.get("instruction") != config["instruction"]:
                raise ValueError("Public task input lost the original instruction")
        stage_module.write_new(stage_dir / "task.json", task)
        stage_module.write_new(stage_dir / "references_not_model_input.json", {
            "prebinding": document, "prebinding_provenance": provenance,
            "route_plan": plan, "slice_audit": audit, "overlay_report": overlay_report,
            "note": "Review evidence only; never a model input and never a reference answer."})
        stage_module.write_new(stage_dir / "HANDOFF_MANIFEST.json", {
            "pipeline_version": PIPELINE_VERSION,
            "input_scope": CROPPED_SCOPE,
            "scene_dir_for_worker": str(scene.resolve()),
            "scene_sha256": hashes,
            "source_sha256": recorded["source_sha256"],
            "task_file": str((stage_dir / "task.json").resolve()),
            "next_step": "The existing collection worker must be invoked manually with a fresh, "
                         "nonexistent destination directory. This pipeline never invokes it.",
            "model_requests_made_by_this_pipeline": 0,
            "expected_model_request_count": context["model_request_accounting"],
            "prebinding_consistency_gate": "The worker enforces the gate after alignment returns "
                                           "and before any skill binding request.",
            "evidence_status": context["evidence_status"]})
        stage_module.record_artifacts(stage_dir, "handoff", [
            "task.json", "references_not_model_input.json", "HANDOFF_MANIFEST.json"])
        stage_module.deliver_stage_result(stage_dir, "handoff", {
            "record_id": task["record_id"], "scene_sha256": hashes})
        return 0
    except Exception as exc:
        stage_module.deliver_failure(stage_dir, "handoff", type(exc).__name__, str(exc))
        return 2
    except BaseException as exc:
        stage_module.deliver_failure(stage_dir, "handoff", type(exc).__name__, str(exc),
                                     {"interrupted": True})
        raise


def cmd_check_binding(args):
    """Post-hoc review of a finished worker destination; the enforced gate is in the worker."""
    stage_dir = stage_module.open_stage(args.run_dir, "check_binding")
    try:
        run_dir = Path(args.run_dir)
        stage_module.require_stage(run_dir, "slice")
        stage_module.require_stage(run_dir, "handoff")
        task = _read(run_dir / "handoff/task.json")
        alignment = _read(args.alignment_json)
        nodes = _read(run_dir / "slice/scene" / SCENE_GRAPH)
        node_ids = [node["id"] for node in nodes]
        try:
            report = binding_module.consistency_gate(task["topology_task_context"], alignment,
                                                     node_ids=node_ids)
        except binding_module.PrebindingMismatch as exc:
            stage_module.write_new(stage_dir / "post_binding_review.json", exc.report)
            if exc.report.get("requires_subgraph_expansion"):
                stage_module.write_new(stage_dir / "requires_subgraph_expansion.json", {
                    "pipeline_version": PIPELINE_VERSION,
                    "reason": "A downstream binding referenced an object outside the supplied crop.",
                    "role_checks": exc.report["role_checks"],
                    "retry_policy": "No automatic expansion or retry; a new run directory and a "
                                    "reviewed scope change are required.",
                    "fabrication": False})
            stage_module.deliver_failure(stage_dir, "check_binding",
                                         "prebinding_consistency_gate_failed",
                                         "; ".join(exc.report["failure_codes"]), exc.report)
            return 3
        stage_module.write_new(stage_dir / "post_binding_review.json", report)
        stage_module.record_artifacts(stage_dir, "check_binding", ["post_binding_review.json"])
        stage_module.deliver_stage_result(stage_dir, "check_binding", {"passed": True})
        return 0
    except Exception as exc:
        stage_module.deliver_failure(stage_dir, "check_binding", type(exc).__name__, str(exc))
        return 2
    except BaseException as exc:
        stage_module.deliver_failure(stage_dir, "check_binding", type(exc).__name__, str(exc),
                                     {"interrupted": True})
        raise


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m embodied_pipeline.topology_filter.cli",
        description="Offline topology input filter; no subcommand contacts a model or a robot.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Validate inputs and render the prebinding prompt")
    prepare.add_argument("--run-dir", required=True)
    prepare.add_argument("--scene-dir", required=True)
    prepare.add_argument("--overlay", required=True)
    prepare.add_argument("--instruction", required=True)
    prepare.add_argument("--record-id", required=True)
    prepare.add_argument("--task-class", default=binding_module.SUPPORTED_TASK_CLASS,
                         choices=(binding_module.SUPPORTED_TASK_CLASS,))
    start = prepare.add_mutually_exclusive_group(required=True)
    start.add_argument("--start-region-id")
    start.add_argument("--start-object-id", type=int)
    prepare.add_argument("--initial-state-json")
    prepare.add_argument(
        "--public-context-json", default=None,
        help="Optional public task input collected with the instruction: the authored "
             "user dialogue and the public spatial context. Validated with the worker's "
             "own reviewed-context validator and handed over unchanged. Omitted by "
             "default, and when omitted the prepared task is identical to a run without "
             "this option. It must never contain a reference answer or a target id.")
    prepare.set_defaults(handler=cmd_prepare)

    ingest = subparsers.add_parser("ingest-binding", help="Validate an externally produced prebinding")
    ingest.add_argument("--run-dir", required=True)
    ingest.add_argument("--from-file", required=True)
    ingest.add_argument("--source", required=True,
                        choices=("manual_fixture", "model_response_file"))
    ingest.add_argument("--candidate-review", default=review_hooks.MODE_OFF,
                        choices=review_hooks.MODES,
                        help="Opt-in second evidence review of each unresolved role, written "
                             "to candidate_review.json. Record-only: the frozen raw file and "
                             "the validated document are unchanged, and the stage stops "
                             "exactly as it did before. Default off.")
    ingest.set_defaults(handler=cmd_ingest_binding)

    route_parser = subparsers.add_parser("route", help="Plan ordered region visits by hop count")
    route_parser.add_argument("--run-dir", required=True)
    route_parser.set_defaults(handler=cmd_route)

    slice_parser = subparsers.add_parser("slice", help="Crop the graph to the routed scope")
    slice_parser.add_argument("--run-dir", required=True)
    slice_parser.add_argument(
        "--extra-keep-ids-json", default=None,
        help="Optional offline id selection from the deterministic context check "
             "(embodied_pipeline.topology_filter.expand). Omitted by default; when omitted "
             "the crop is byte-identical to a run without this option.")
    slice_parser.set_defaults(handler=cmd_slice)

    handoff_parser = subparsers.add_parser("handoff", help="Write the cropped-scope task")
    handoff_parser.add_argument("--run-dir", required=True)
    handoff_parser.set_defaults(handler=cmd_handoff)

    check = subparsers.add_parser("check-binding", help="Post-hoc review of a worker destination")
    check.add_argument("--run-dir", required=True)
    check.add_argument("--alignment-json", required=True)
    check.set_defaults(handler=cmd_check_binding)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
