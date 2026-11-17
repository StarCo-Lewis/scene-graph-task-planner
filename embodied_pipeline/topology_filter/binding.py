"""Prebinding contract, offline ingest and the enforced consistency gate.

The prebinding is produced outside this module (one external model request, or a
reviewed offline fixture). Nothing here calls a model or the network.

Scope. The prebinding vocabulary covers every task role (source object, origin,
destination, constraint object, visit target), because the crop needs all of them.
What is deliberately narrow is the *order* gate: it only accepts milestones taken
from unambiguous single-target navigation subtasks. The role references inside a
manipulation step are never read as a visit order; when milestones or their
dependencies cannot be established the gate reports ``order_unverified`` and still
delivers its JSON. Navigation is the first validated task class; manipulation
support is present in the vocabulary only and is not claimed to be verified.

The gate compares the prebinding with the downstream alignment per role. It never
compares the union of bound ids, because an unordered id set hides a swap between
two roles. The role-text comparison it performs is lexical consistency only; it is
not an independent semantic verification.
"""

import copy
import hashlib
import re

from . import region_phrase
from ..contracts import text_budget

BINDING_VERSION = "topology_prebinding_v1"
GATE_VERSION = "topology_prebinding_consistency_gate_v3"
ROLE_IDENTITY_POLICY = "locked_wire_roles_unique_bijection_v1"
#: Which revision of the step-phrase recognition the gate used. Bumped together with
#: GATE_VERSION when the recognised navigation wording changes, so an old report and a
#: new one are never silently compared. The accepted *document* shape is unchanged.
STEP_PHRASE_REVISION = "navigation_step_phrases_20261107_explicit_prohibitions"
#: Which revision of the region cross-checks ``validate_prebinding`` applied. The
#: accepted document shape - and therefore ``BINDING_VERSION`` - is unchanged: these
#: checks only refuse documents whose fields contradict the overlay or the room the
#: instruction itself named.
REGION_CHECK_REVISION = "prebinding_region_consistency_v1_20261104"
#: A role's ``evidence`` is optional: omitted, or one short sentence. Since 2026-11-08
#: its length is measured only and never refuses a document (revision ``..._v2``); the
#: text is moved out of the engineering JSON into a separate audit log. The constants
#: below are the historical v1 word budget, kept so earlier refusals stay classifiable.
#: The structural checks - statuses, candidates, questions, rooms, stops - are unchanged.
EVIDENCE_MAX_WORDS = text_budget.EVIDENCE_MAX_WORDS
EVIDENCE_WORD_RULE = text_budget.WORD_RULE
#: Separate from the word budget and never called a word count.
EVIDENCE_MAX_BYTES = text_budget.EVIDENCE_MAX_BYTES
#: Which revision of the evidence policy this validator applies.
EVIDENCE_BUDGET_REVISION = "prebinding_evidence_optional_unbounded_audit_log_20261108_v2"
EVIDENCE_LENGTH_LIMIT_ENFORCED = False
#: A clarification question is a question for a person, not a derivation.
CLARIFICATION_QUESTION_MAX_CHARS = 700
ROLE_ID_PATTERN = re.compile(r"^role_[0-9]{3,}$")
ROLE_KINDS = ("source_object", "origin", "destination", "constraint_object", "visit_target")
# A constraint role is never a visit target; every other kind may be visited.
VISIT_KINDS = ("source_object", "origin", "destination", "visit_target")
ROLE_STATUSES = ("bound", "ambiguous", "missing_environment_information",
                 "missing_user_information")
QUESTION_REQUIRED_STATUSES = ("ambiguous", "missing_user_information")
SUPPORTED_TASK_CLASS = "ordered_navigation_visits_only"
# Why a prebinding stopped. ``unsupported_task_shape`` records a capability limit of
# this filter version; it must never be reported as a missing user utterance.
STOP_REASONS = ("clarification_required", "missing_environment_information",
                "missing_user_information", "needs_environment_context",
                "unsupported_task_shape")
# Explicit action evidence. A single-role step is not navigation by itself:
# "grasp the cup" also references exactly one role. These phrases are matched only
# as the *leading* action phrase of a clause (or of the declared ``action`` field),
# on word boundaries. They are never searched inside target names or subtask ids,
# so "navigate to the open cabinet" stays navigation instead of being read as an
# open action, and "go to the fireplace" is not read as a place action.
NAVIGATION_PHRASES = ("navigate", "go to", "go back to", "move to", "move the robot",
                      "move toward", "move towards", "walk to", "travel to", "drive to",
                      "approach", "return to", "head to", "proceed to", "visit",
                      "reach the", "arrive at", "come to", "come back to")
MANIPULATION_PHRASES = ("grasp", "pick", "place", "put", "lift", "lower", "open", "close",
                        "push", "pull", "release", "hand over", "handover", "carry",
                        "transfer", "inspect", "set down", "drop", "position the arm",
                        "align the arm", "hold")
# Surface forms of the head verb of a whitelisted phrase. This is a closed, explicit
# table, never a suffix-stripping guess: "holder" must not be read as "hold", and
# "opening" must stay a manipulation word. Inflecting the manipulation verbs too is
# deliberate - it is what lets "end by placing the cup" be recognised as manipulation
# instead of falling through to an unclassified step.
VERB_INFLECTIONS = {
    "navigate": ("navigates", "navigated", "navigating"),
    "go": ("goes", "going", "went"),
    "move": ("moves", "moved", "moving"),
    "walk": ("walks", "walked", "walking"),
    "travel": ("travels", "travelled", "traveled", "travelling", "traveling"),
    "drive": ("drives", "drove", "driving"),
    "approach": ("approaches", "approached", "approaching"),
    "return": ("returns", "returned", "returning"),
    "head": ("heads", "headed", "heading"),
    "proceed": ("proceeds", "proceeded", "proceeding"),
    "visit": ("visits", "visited", "visiting"),
    "reach": ("reaches", "reached", "reaching"),
    "arrive": ("arrives", "arrived", "arriving"),
    "come": ("comes", "came", "coming"),
    "grasp": ("grasps", "grasped", "grasping"),
    "pick": ("picks", "picked", "picking"),
    "place": ("places", "placed", "placing"),
    "put": ("puts", "putting"),
    "lift": ("lifts", "lifted", "lifting"),
    "lower": ("lowers", "lowered", "lowering"),
    "open": ("opens", "opened", "opening"),
    "close": ("closes", "closed", "closing"),
    "push": ("pushes", "pushed", "pushing"),
    "pull": ("pulls", "pulled", "pulling"),
    "release": ("releases", "released", "releasing"),
    "hand": ("hands", "handed", "handing"),
    "carry": ("carries", "carried", "carrying"),
    "transfer": ("transfers", "transferred", "transferring"),
    "inspect": ("inspects", "inspected", "inspecting"),
    "set": ("sets", "setting"),
    "drop": ("drops", "dropped", "dropping"),
    "hold": ("holds", "held", "holding"),
    "position": ("positions", "positioned", "positioning"),
    "align": ("aligns", "aligned", "aligning"),
}


def _phrase_forms(phrase, kind):
    """One whitelisted phrase plus the inflected forms of its head verb."""
    words = phrase.split()
    forms = [(tuple(words), kind)]
    for inflected in VERB_INFLECTIONS.get(words[0], ()):
        forms.append(((inflected,) + tuple(words[1:]), kind))
    return forms


# Longest phrase first, so "go back to" is preferred over "go to" and "come back to"
# over "come to". Matching stays whole-token and anchored at the head of a clause.
ACTION_PHRASE_KINDS = tuple(sorted(
    [form for phrase in NAVIGATION_PHRASES for form in _phrase_forms(phrase, "navigation")]
    + [form for phrase in MANIPULATION_PHRASES
       for form in _phrase_forms(phrase, "manipulation")],
    key=lambda item: (-len(item[0]), -len(" ".join(item[0])), item[0])))
# Words that frame the *last* step of a round ("finish ...", "end ..."). On their own
# they carry no action evidence at all: "finish cleaning the bench" is not navigation.
# They are only ever a prefix, and what follows them must supply the evidence.
TERMINAL_FRAMING_WORDS = frozenset((
    "finish", "finishes", "finished", "finishing",
    "end", "ends", "ended", "ending",
    "conclude", "concludes", "concluded", "concluding"))
#: "finish **by** navigating to X" - the continuation must itself be an action phrase,
#: and a manipulation continuation stays manipulation.
TERMINAL_CONNECTORS = frozenset(("by", "with"))
#: "finish **at** X" - a purely locative terminal with no verb of its own. "by" is
#: deliberately absent here so that "end by placing ..." can only take the connector
#: branch, where "placing" is recognised as manipulation.
TERMINAL_LOCATIVES = frozenset(("at", "in", "on", "near", "beside", "alongside"))
STOPWORDS = frozenset((
    "the", "a", "an", "of", "to", "in", "on", "at", "with", "and", "or", "for", "from", "then",
    "go", "visit", "please", "first", "next", "finally", "robot", "object", "item", "thing",
    "near", "into", "onto", "its", "it", "this", "that", "there", "here", "room"))


class BindingError(ValueError):
    """The prebinding document is unusable; never repaired silently."""


class PrebindingMismatch(ValueError):
    """The downstream alignment disagrees with the locked prebinding, or cannot be verified."""

    def __init__(self, report):
        super().__init__("Prebinding consistency gate failed: "
                         + ", ".join(report["failure_codes"]))
        self.report = report


def tokens(value):
    """Lowercase content tokens from a string or a list of strings."""
    if isinstance(value, str):
        parts = re.split(r"[^a-z0-9]+", value.lower())
    elif isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            parts.extend(re.split(r"[^a-z0-9]+", item.lower()) if isinstance(item, str) else [])
    else:
        parts = []
    return {part for part in parts if part and part not in STOPWORDS and not part.isdigit()}


def instruction_sha256(instruction):
    return hashlib.sha256(instruction.encode("utf-8")).hexdigest()


def prebinding_contract_text():
    """Role contract echoed to the downstream decomposition prompt (no answers)."""
    return (
        "Role identity contract " + BINDING_VERSION + ": role_id values of the form role_001 are "
        "stable task-level role identifiers. Preserve each supplied role_id together with its "
        "semantic description; do not renumber, merge, drop or invent roles, and do not reuse one "
        "role_id for two different physical objects. The order of the role list is not the order of "
        "actions: ordered visits come from the ordered visit list, which keeps a revisit as a "
        "separate visit with its own dependency on the preceding visit.")


def locked_role_wire_contract(context):
    """Explicit definition slots; action order never determines role numbering."""
    roles = sorted(context["prebound_roles"], key=lambda row: row["role_id"])
    if [row["role_id"] for row in roles] != [
            "role_%03d" % number for number in range(1, len(roles) + 1)]:
        raise ValueError("Locked wire roles require consecutive role IDs starting at role_001")
    return {
        "policy": ROLE_IDENTITY_POLICY,
        "definition_slots": [
            {"definition_position": position, "role_id": row["role_id"],
             "role_text": row["role_text"]}
            for position, row in enumerate(roles, 1)],
        "rule": "In the typed decomposition roles table, use the kth issued role code for "
                "the kth definition slot listed here. Keep these definition rows in slot "
                "order even when visits occur in another order. In tasks/task_roles use "
                "the corresponding issued code for each visit. Do not renumber by action "
                "order. Copy each role_text, optionally omitting definite articles, into "
                "its name and semantic query. Labels are categories, not role identity.",
        "raw_output_must_be_preserved": True,
    }


# --------------------------------------------------------------------------- #
# Prebinding document
# --------------------------------------------------------------------------- #

def _active_state(node):
    """True/False/None, where None means the node does not declare the flag.

    An undeclared flag keeps the existing pipeline convention of assuming active
    (some supported graphs carry no ``active`` field); the assumption is recorded
    rather than hidden.
    """
    value = node.get("active")
    return value if isinstance(value, bool) else None


def _check_object(node_by_id, object_id, label, errors, assumptions):
    if node_by_id is None:
        return
    node = node_by_id.get(object_id)
    if node is None:
        errors.append(label + " references object " + str(object_id)
                      + " which is not in the supplied graph")
        return
    state = _active_state(node)
    if state is None:
        assumptions.append({"object_id": object_id, "role_label": label,
                            "assumed_active_when_unspecified": True,
                            "basis": "existing pipeline convention for graphs without an "
                                     "active flag"})
    elif state is False:
        errors.append(label + " references inactive object " + str(object_id))


def _lookup_object_id(role):
    """The role's ``object_id`` when it can be used as an index key, else None.

    A malformed value - an array, an object, a string, a bool - is reported by the
    per-role type checks above. The region checks must not be the place where it
    turns into a TypeError from an unhashable dict key, so they look the id up
    through this guard and simply find nothing.
    """
    value = role.get("object_id")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def validate_prebinding(document, instruction, overlay=None, nodes=None,
                        task_class=SUPPORTED_TASK_CLASS):
    """Validate a prebinding document against the locked instruction.

    Role list order is explicitly not action order; the ordered walk is carried
    only by ``visit_role_ids``, which keeps every revisit as its own entry.

    When an ``overlay`` is supplied the region cross-checks of
    :data:`REGION_CHECK_REVISION` also apply: a declared ``region_id`` must agree
    with the recorded index, and a room qualifier found in a role's own
    ``role_text`` must agree with where the index records the bound object, and
    must not be contradicted by candidates the index places in another room. Two
    things are deliberately *not* done. An object with no recorded room is never
    excluded by a qualifier and never assigned a room - that case is the existing
    ``needs_environment_context`` stop. And a genuine ambiguity between two
    objects that both still fit is never resolved: the checks here only refuse
    documents that contradict the recorded index or their own instruction, never
    documents that correctly report that they cannot decide.
    """
    errors = []
    assumptions = []
    if not isinstance(document, dict):
        raise BindingError("Prebinding must be a JSON object")
    if task_class != SUPPORTED_TASK_CLASS:
        raise BindingError("Unsupported task class for this version: " + repr(task_class)
                           + "; only " + SUPPORTED_TASK_CLASS + " is implemented")
    if document.get("binding_version") != BINDING_VERSION:
        errors.append("binding_version must be " + BINDING_VERSION)
    if document.get("task_class") not in (None, SUPPORTED_TASK_CLASS):
        errors.append("task_class must be " + SUPPORTED_TASK_CLASS)
    if document.get("instruction") != instruction:
        errors.append("Prebinding instruction differs from the locked task instruction")
    if document.get("instruction_sha256") != instruction_sha256(instruction):
        errors.append("instruction_sha256 does not match the locked instruction")
    if document.get("role_order_is_not_action_order") is not True:
        errors.append("role_order_is_not_action_order must be true")
    roles = document.get("roles")
    if not isinstance(roles, list) or not roles:
        raise BindingError("; ".join(errors + ["roles must be a nonempty array"]))
    node_by_id = None if nodes is None else {node["id"]: node for node in nodes}
    by_role = {}
    for role in roles:
        if not isinstance(role, dict):
            errors.append("Each role must be an object")
            continue
        role_id = role.get("role_id")
        if not (isinstance(role_id, str) and ROLE_ID_PATTERN.match(role_id)):
            errors.append("role_id must look like role_001: " + repr(role_id))
            continue
        if role_id in by_role:
            errors.append("Duplicate role_id: " + role_id)
            continue
        by_role[role_id] = role
        if role.get("role_kind") not in ROLE_KINDS:
            errors.append(role_id + ".role_kind must be one of " + repr(ROLE_KINDS))
        status = role.get("status")
        if status not in ROLE_STATUSES:
            errors.append(role_id + ".status is invalid")
        role_text = role.get("role_text")
        if not (isinstance(role_text, str) and role_text.strip()):
            errors.append(role_id + ".role_text must be a nonempty string")
        elif role_text not in instruction:
            errors.append(role_id + ".role_text is not an exact substring of the instruction")
        labels = role.get("expected_labels")
        if not (isinstance(labels, list) and labels
                and all(isinstance(item, str) and item.strip() for item in labels)):
            errors.append(role_id + ".expected_labels must be a nonempty array of strings")
        question = role.get("clarification_question")
        if status in QUESTION_REQUIRED_STATUSES:
            if not (isinstance(question, str) and question.strip()):
                errors.append(role_id + " has status " + str(status)
                              + " and therefore requires a concrete clarification_question")
        elif question is not None:
            errors.append(role_id + " carries a clarification_question without an unresolved status")
        # Evidence prose is optional and its length no longer decides acceptance; the
        # structured fields carry the decision and the text goes to the audit log.
        if isinstance(question, str) and len(question) > CLARIFICATION_QUESTION_MAX_CHARS:
            errors.append(role_id + ".clarification_question is " + str(len(question))
                          + " characters and the limit is "
                          + str(CLARIFICATION_QUESTION_MAX_CHARS) + "; it must be a question "
                          "asked of a person, not a derivation of the answer")
        if status == "bound":
            object_id = role.get("object_id")
            if not isinstance(object_id, int) or isinstance(object_id, bool):
                errors.append(role_id + ".object_id must be an integer when bound")
            else:
                _check_object(node_by_id, object_id, role_id, errors, assumptions)
            if role.get("candidate_object_ids"):
                errors.append(role_id + " is bound but still lists competing candidates")
        else:
            if role.get("object_id") is not None:
                errors.append(role_id + " is not bound but carries an object_id")
            candidates = role.get("candidate_object_ids")
            if status == "ambiguous":
                if not (isinstance(candidates, list) and len(candidates) >= 2):
                    errors.append(role_id + " is ambiguous and needs at least two candidate ids")
                else:
                    integers = []
                    for candidate in candidates:
                        if not isinstance(candidate, int) or isinstance(candidate, bool):
                            errors.append(role_id + " has a non-integer candidate id")
                        else:
                            integers.append(candidate)
                            _check_object(node_by_id, candidate, role_id + " candidate", errors,
                                          assumptions)
                    # [1, 1] is a repeated id, not a competing alternative.
                    if len(set(integers)) < 2:
                        errors.append(role_id + " is ambiguous and needs at least two distinct "
                                      + "candidate ids, got " + repr(candidates))
            elif candidates:
                errors.append(role_id + " must not list candidates for status " + str(status))
    unresolved = sorted(rid for rid, role in by_role.items() if role.get("status") != "bound")
    stop = document.get("stop_execution")
    if not isinstance(stop, bool):
        errors.append("stop_execution must be a boolean")
    elif unresolved and not stop:
        errors.append("Unresolved roles require stop_execution=true: " + repr(unresolved))
    visits = document.get("visit_role_ids")
    if not isinstance(visits, list):
        errors.append("visit_role_ids must be an array")
        visits = []
    elif stop:
        if visits:
            errors.append("A stopped prebinding must not carry a partial visit order")
    else:
        if not visits:
            errors.append("visit_role_ids must be nonempty when execution is not stopped")
        for role_id in visits:
            role = by_role.get(role_id)
            if role is None:
                errors.append("visit_role_ids references an undefined role: " + repr(role_id))
            elif role.get("status") != "bound":
                errors.append("visit_role_ids references an unbound role: " + role_id)
            elif role.get("role_kind") not in VISIT_KINDS:
                errors.append("Role kind cannot be a visit target: " + role_id)
        if visits and document.get("terminal_role_id") != visits[-1]:
            errors.append("terminal_role_id must be the last entry of visit_role_ids")
        if visits and not isinstance(document.get("terminal_robot_location_required"), bool):
            errors.append("terminal_robot_location_required must be an explicit boolean; the "
                          "terminal role is only the last visit target and does not by itself "
                          "imply a terminal robot-near goal")
    if stop:
        stop_reason = document.get("stop_reason")
        if stop_reason is not None and stop_reason not in STOP_REASONS:
            errors.append("stop_reason must be one of " + repr(STOP_REASONS))
        if stop_reason == "unsupported_task_shape":
            if not (isinstance(document.get("stop_detail"), str)
                    and document["stop_detail"].strip()):
                errors.append("unsupported_task_shape requires a concrete stop_detail describing "
                              "the unsupported shape; it is a filter capability limit, not a "
                              "missing user utterance")
            if any(role.get("status") == "missing_user_information"
                   for role in by_role.values()):
                errors.append("An unsupported task shape must not be reported as "
                              "missing_user_information")
        else:
            questions = [role.get("clarification_question") for role in by_role.values()
                         if role.get("status") in QUESTION_REQUIRED_STATUSES]
            has_question = bool(document.get("clarification_question")) or any(questions)
            if not (has_question or document.get("missing_information")):
                errors.append("A stopped prebinding must carry a clarification question or "
                              "missing information")
            if any(role.get("status") in QUESTION_REQUIRED_STATUSES
                   for role in by_role.values()) and not any(questions):
                errors.append("An ambiguous or user-missing role requires its own concrete "
                              "question")
    if not stop and document.get("clarification_question"):
        errors.append("A clarification question requires stop_execution=true")
    # The decidable half of the final-state table the request contract states: the
    # fields that move together. A document that sets a stop-only field while it
    # continues, or keeps a terminal goal while it stops, contradicts itself whichever
    # half was meant - and the half that is wrong cannot be guessed, so neither is
    # repaired. Absent optional fields are left alone: omitting one is not a
    # contradiction, and this check never requires a key to be present.
    if isinstance(stop, bool) and not stop:
        if document.get("needs_environment_context") is True:
            errors.append("needs_environment_context=true requires stop_execution=true; a room "
                          "the graph does not record cannot be recovered while continuing")
        if document.get("stop_reason") is not None:
            errors.append("stop_reason is set while stop_execution is false: "
                          + repr(document.get("stop_reason")))
        if document.get("missing_information"):
            errors.append("missing_information is nonempty while stop_execution is false; "
                          "information the run is missing requires stopping")
    elif isinstance(stop, bool) and stop:
        if document.get("terminal_role_id") is not None:
            errors.append("A stopped prebinding must not carry a terminal_role_id: "
                          + repr(document.get("terminal_role_id")))
        if document.get("terminal_robot_location_required") is True:
            errors.append("A stopped prebinding must not require a terminal robot location; "
                          "there is no terminal visit to require it of")
    region_qualifiers = {}
    if overlay is not None:
        assignments = {entry["object_id"]: entry for entry in overlay["object_region_index"]}
        region_ids = {region["region_id"] for region in overlay.get("regions") or []}
        vocabulary = region_phrase.region_vocabulary(overlay)
        for role_id, role in sorted(by_role.items()):
            role_status = role.get("status")
            entry = assignments.get(_lookup_object_id(role))
            # (a) A declared region_id is re-read from the index rather than trusted.
            # Supplying it stays optional, because a response that leaves it out is
            # not wrong; supplying a value the index contradicts is refused.
            declared = role.get("region_id")
            if declared is not None:
                # A non-string region_id is a type error, reported as one. It must not
                # reach the set lookup, where an array or object raises TypeError
                # instead of being collected like every other refusal.
                if not isinstance(declared, str):
                    errors.append(role_id + ".region_id must be a string or null, got "
                                  + type(declared).__name__ + ": " + repr(declared))
                elif declared not in region_ids:
                    errors.append(role_id + ".region_id is not a region the overlay records: "
                                  + repr(declared))
                elif role_status != "bound":
                    errors.append(role_id + " is not bound but declares region_id "
                                  + repr(declared) + "; an unresolved role has no single object "
                                  "whose room could be stated")
                elif entry is None or entry.get("status") != "assigned":
                    errors.append(role_id + " declares region_id " + repr(declared)
                                  + " for an object the index gives no assigned region; region_id "
                                  "must be null and the document must stop with "
                                  "needs_environment_context=true")
                elif entry.get("region_id") != declared:
                    errors.append(role_id + ".region_id is " + repr(declared) + " but the index "
                                  "records " + repr(entry.get("region_id")) + " for object "
                                  + repr(role.get("object_id")))
            if role_status == "bound" and _lookup_object_id(role) is not None:
                if entry is None:
                    errors.append(role_id + " binds an object that is absent from the overlay index")
                elif entry.get("status") != "assigned":
                    # The room is never guessed; the caller reports needs_environment_context.
                    if document.get("needs_environment_context") is not True or not stop:
                        errors.append(role_id + " binds an object with an unknown region, which requires "
                                      "needs_environment_context=true and stop_execution=true")
            # (b) A room the instruction itself named. The qualifier is read from
            # this role's own role_text only - never from a room name occurring
            # somewhere else in the instruction - and an unrecognised wording
            # leaves the role checked exactly as before.
            analysis = region_phrase.analyze_role_text(role.get("role_text"), vocabulary)
            region_qualifiers[role_id] = analysis
            if not analysis["recognized"]:
                continue
            if role_status == "bound":
                if (entry is not None and entry.get("status") == "assigned"
                        and entry.get("region_id") != analysis["region_id"]):
                    errors.append(role_id + " binds object " + repr(role.get("object_id"))
                                  + ", which the index records in "
                                  + repr(entry.get("region_id")) + ", but its role_text restricts "
                                  "it to " + repr(analysis["source_room_name"]) + " ("
                                  + analysis["region_id"] + ")")
                # An object with no recorded region is handled by the unknown-region
                # rule above; a room qualifier cannot exclude it and must not be
                # turned into a claim about where it is.
            elif role_status == "ambiguous":
                # Only a list is iterated. A scalar in this field is already reported
                # by the type checks above and must not raise TypeError here.
                supplied = role.get("candidate_object_ids")
                candidates = [item for item in (supplied if isinstance(supplied, list) else [])
                              if isinstance(item, int) and not isinstance(item, bool)]
                groups = region_phrase.classify_candidates(analysis["region_id"], candidates,
                                                           assignments)
                conflicting = groups[region_phrase.OTHER_KNOWN_REGION]
                if conflicting:
                    errors.append(role_id + " is ambiguous between " + repr(sorted(candidates))
                                  + ", but its role_text restricts it to "
                                  + repr(analysis["source_room_name"]) + " ("
                                  + analysis["region_id"] + ") and the index records "
                                  + repr(conflicting) + " in a different room, so those are not "
                                  "competing candidates")
                # Candidates with no recorded room stay candidates: the qualifier
                # cannot exclude them, so two or more objects still fitting inside
                # the named room remains a genuine ambiguity.
    if errors:
        raise BindingError("; ".join(errors))
    return {"roles": by_role, "visit_role_ids": list(visits), "stop_execution": bool(stop),
            "unresolved_role_ids": unresolved, "task_class": SUPPORTED_TASK_CLASS,
            "stop_reason": document.get("stop_reason"),
            "terminal_robot_location_required":
                bool(document.get("terminal_robot_location_required")),
            "active_assumptions": assumptions,
            "region_qualifiers": region_qualifiers,
                "region_check_revision": REGION_CHECK_REVISION,
                "evidence_budget_revision": EVIDENCE_BUDGET_REVISION,
                "evidence_length_limit_enforced": EVIDENCE_LENGTH_LIMIT_ENFORCED}


def ordered_visit_objects(document):
    """Ordered visit targets as object ids, every revisit preserved."""
    roles = {role["role_id"]: role for role in document["roles"]}
    return [{"role_id": role_id, "object_id": roles[role_id]["object_id"]}
            for role_id in document["visit_role_ids"]]


# --------------------------------------------------------------------------- #
# Consistency gate
# --------------------------------------------------------------------------- #

def _decomposition_roles(decomposition):
    """role_id -> lexical descriptor from task_roles/object_requirements and subtasks."""
    found = {}
    pools = []
    for key in ("task_roles", "object_requirements", "constraint_roles"):
        value = decomposition.get(key)
        if isinstance(value, list):
            pools.append(value)
    for subtask in decomposition.get("subtasks") or []:
        if isinstance(subtask, dict) and isinstance(subtask.get("required_roles"), list):
            pools.append(subtask["required_roles"])
    for pool in pools:
        for role in pool:
            if not isinstance(role, dict) or not isinstance(role.get("role_id"), str):
                continue
            entry = found.setdefault(role["role_id"], {"role_id": role["role_id"], "terms": set()})
            for key in ("name", "semantic_query_en", "semantic_queries_en", "semantic_labels"):
                entry["terms"] |= tokens(role.get(key))
    return found


def _dependency_reachability(decomposition):
    """``(reachable, error)`` where ``reachable[a]`` is every subtask a transitively depends on."""
    subtasks = decomposition.get("subtasks")
    if not isinstance(subtasks, list) or not subtasks:
        return None, "subtasks_missing"
    declared = {}
    for subtask in subtasks:
        if not isinstance(subtask, dict) or not isinstance(subtask.get("subtask_id"), str):
            return None, "subtask_id_missing"
        if subtask["subtask_id"] in declared:
            return None, "duplicate_subtask_id"
        dependencies = subtask.get("dependencies")
        if dependencies is None:
            dependencies = []
        if not isinstance(dependencies, list) or any(not isinstance(d, str) for d in dependencies):
            return None, "dependencies_malformed"
        declared[subtask["subtask_id"]] = list(dependencies)
    for subtask_id, dependencies in declared.items():
        for dependency in dependencies:
            if dependency not in declared:
                return None, "dependency_reference_invalid"
            if dependency == subtask_id:
                return None, "self_dependency"
    reachable = {subtask_id: set() for subtask_id in declared}
    for subtask_id in declared:
        seen, frontier = set(), list(declared[subtask_id])
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            if current == subtask_id:
                return None, "dependency_cycle"
            frontier.extend(declared[current])
        reachable[subtask_id] = seen
    return reachable, None


# A clause boundary. Only clause-initial action phrases are classified, so the
# words of a target name ("the open cabinet", "fireplace") are never actions.
CLAUSE_SPLIT_RE = re.compile(r"[,;.:!?()\[\]/]"
                             r"|\band then\b|\bthen\b|\band\b|\bbefore\b|\bafter\b"
                             r"|\bwhile\b|\bwhilst\b|\bso that\b|\bin order to\b")
WORD_RE = re.compile(r"[a-z]+")
# Fillers that may precede the action phrase of a clause.
LEADING_FILLER = frozenset(("then", "first", "next", "finally", "lastly", "now", "also",
                            "please", "afterwards", "subsequently", "second", "third",
                            "again", "the", "robot", "you", "should", "must"))

# Only a complete, explicitly negated object-action list is removed from action
# classification. "but/then/while" and unrecognised targets cannot enter this pattern.
_PROHIBITED_VERB = r"(?:pick up|grasp|lift|move|touch|manipulate|carry|push|pull|open|close)"
_PROHIBITED_TARGET = r"(?:the objects?|any objects?|objects?|anything|it|them)"
_PROHIBITED_ITEM = _PROHIBITED_VERB + r"(?:\s+" + _PROHIBITED_TARGET + r")?"
PROHIBITION_RE = re.compile(
    r"\b(?:do not|don't|never)\s+" + _PROHIBITED_ITEM
    + r"(?:(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)"
    + _PROHIBITED_ITEM + r")*(?=\s*(?:[.;!?]|$))", re.I)


def explicit_prohibitions(text):
    """Original spans, for audit; this does not create a structured model constraint."""
    return [{"text": match.group(0), "start": match.start(), "end": match.end(),
             "scope": "explicit_negated_object_actions",
             "physical_compliance_verified": False}
            for match in PROHIBITION_RE.finditer(str(text or ""))]


def _positive_action_text(text):
    return PROHIBITION_RE.sub(" ", str(text or ""))


def _action_tokens(value):
    """Lowercase letters-only tokens: ids, digits and punctuation never form a verb."""
    return WORD_RE.findall(str(value or "").lower())


def _match_phrase(head):
    """Kind of the whitelisted action phrase this token run starts with, or None."""
    for words, kind in ACTION_PHRASE_KINDS:
        if tuple(head[:len(words)]) == words:
            return kind
    return None


#: Placeholder put in place of a declared target name. It is not a word, so it can
#: never begin an action phrase and never joins two neighbouring token runs.
_TARGET_MASK = "\x00target"


def _target_spans(subtask):
    """Token runs of the target names this step itself declares.

    Taken only from the step's own ``required_roles``: each role's ``name`` and its
    ``semantic_labels``. These are the words the model already told us are an object
    name, so they are the one place where "open", "drop" or "place" is known to be
    part of a noun phrase rather than an action.
    """
    spans = set()
    for role in subtask.get("required_roles") or []:
        if not isinstance(role, dict):
            continue
        values = [role.get("name")]
        labels = role.get("semantic_labels")
        if isinstance(labels, list):
            values.extend(labels)
        for value in values:
            span = tuple(_action_tokens(value))
            if span:
                spans.add(span)
    # Longest first, so "open cabinet" is masked before a bare "cabinet" could be.
    return tuple(sorted(spans, key=lambda span: (-len(span), span)))


def _mask_targets(tokens, spans):
    """``tokens`` with every occurrence of a declared target name replaced by a mask.

    This is what keeps the original principle intact: the words of a target name are
    never scanned for actions, so "finish at the open cabinet" and "end at the
    drop-leaf table" stay navigation while "end at the shelf holding the cup" does
    not. No span is inferred from the graph or guessed from word lists; only names
    the step declared for itself are masked.
    """
    result = list(tokens)
    for span in spans:
        index, width = 0, len(span)
        while index + width <= len(result):
            if tuple(result[index:index + width]) == span:
                result[index:index + width] = [_TARGET_MASK]
                index += 1
            else:
                index += 1
    return result


def _terminal_framing_kind(head, spans):
    """Kind of a clause that opens with a terminal framing word, or None.

    Two bounded shapes are accepted, and nothing else:

    * ``finish|end|conclude`` + optional ``by``/``with`` + a whitelisted action
      phrase. The kind is that phrase's own kind, so "end by placing the cup on the
      shelf" is *manipulation*, not navigation, and a step that also navigates
      therefore becomes ``mixed``.
    * ``finish|end|conclude`` + a locative preposition and, *outside the target
      names this step declared*, no manipulation phrase: "finish at the folded
      paper map". A manipulation phrase that survives the masking withdraws the
      reading, so "end at the shelf holding the cup" stays unclassified. When the
      step declares no target name there is nothing to mask and the whole remainder
      is scanned, which can only make the reading stricter.

    Everything else returns None. In particular a framing word followed by a verb
    this module does not know - "finish cleaning the bench", "end tidying up" - has
    no navigation evidence and is reported as unclassified, which blocks the order
    check rather than passing it.
    """
    if not head or head[0] not in TERMINAL_FRAMING_WORDS:
        return None
    rest = head[1:]
    if rest and rest[0] in TERMINAL_CONNECTORS:
        return _match_phrase(rest[1:])
    if rest and rest[0] in TERMINAL_LOCATIVES:
        tail = _mask_targets(rest[1:], spans)
        if not tail:
            return None
        if any(_match_phrase(tail[offset:]) == "manipulation" for offset in range(len(tail))):
            return None
        return "navigation"
    return None


def _leading_action_kind(tokens, spans=()):
    """Kind of one clause, from its leading action phrase only, on word boundaries."""
    index = 0
    while index < len(tokens) and tokens[index] in LEADING_FILLER:
        index += 1
    head = tokens[index:]
    if head[:2] == ["not", "only"]:
        head = head[2:]
    if not head:
        return None
    direct = _match_phrase(head)
    if direct is not None:
        return direct
    return _terminal_framing_kind(head, spans)


def _step_kind(subtask):
    """``navigation``, ``manipulation``, ``mixed`` or ``unknown`` for one step.

    The kind comes from the declared ``action`` field and from the leading action
    phrase of each clause of the step text, matched on whole words. Target names
    and subtask ids are never scanned, so "navigate to the open cabinet" is
    navigation rather than an open action, "go to the fireplace" is not a place
    action, and a ``subtask_id`` such as ``visit_001`` carries no evidence.

    A step whose clauses declare both kinds ("go to A and pick up B"), or whose
    ``action`` field contradicts its text, is ``mixed``: it is never accepted as a
    reliable single navigation step. Clauses with no leading action phrase (noun
    or prepositional fragments) contribute nothing.

    The target names the step declares in its own ``required_roles`` are masked out
    before a terminal locative clause is scanned, so an object called "open
    cabinet" or "drop-leaf table" is still read as a place and not as an action.
    """
    kinds = set()
    declared = _leading_action_kind(_action_tokens(
        _positive_action_text(subtask.get("action"))))
    if declared:
        kinds.add(declared)
    spans = _target_spans(subtask)
    for key in ("description", "name", "summary"):
        raw = _positive_action_text(subtask.get(key)).lower()
        if not raw.strip():
            continue
        for clause in CLAUSE_SPLIT_RE.split(raw):
            kind = _leading_action_kind(_action_tokens(clause), spans)
            if kind:
                kinds.add(kind)
    if len(kinds) > 1:
        return "mixed"
    if kinds:
        return kinds.pop()
    return "unknown"


def _navigation_milestones(decomposition, visit_role_ids):
    """Milestones taken only from steps that declare themselves to be navigation.

    A step qualifies when its own text identifies it as navigation and its
    required_roles reference exactly one role, which is a visit target. A step
    that touches a visit role while naming several roles makes the order
    unestablished, whatever its kind. A step recognised as pure manipulation on a
    single role is not a visit and is skipped. A step that touches a visit role but
    cannot be classified, or that mixes navigation with another action, also makes
    the order unestablished: ``None`` is returned so the caller reports
    ``order_unverified`` instead of guessing from word or list order.
    """
    wanted = set(visit_role_ids)
    milestones = []
    for subtask in decomposition.get("subtasks") or []:
        if not isinstance(subtask, dict):
            return None
        referenced = sorted({role.get("role_id") for role in subtask.get("required_roles") or []
                             if isinstance(role, dict) and isinstance(role.get("role_id"), str)})
        visited = [role_id for role_id in referenced if role_id in wanted]
        if not visited:
            continue
        if len(referenced) > 1:
            # The step involves more than one role, so it is not an unambiguous
            # single-target navigation step and its approached target is unknown.
            return None
        kind = _step_kind(subtask)
        if kind == "manipulation":
            # Not a visit; it does not contribute to the visit order.
            continue
        if kind != "navigation":
            # ``unknown`` or ``mixed``: the step may or may not be a visit.
            return None
        milestones.append({"subtask_id": subtask["subtask_id"], "role_id": visited[0]})
    return milestones


def _milestone_chain(milestones, reachable):
    """Order milestones into a single dependency chain, or None when they do not form one.

    ``reachable[x]`` holds the subtasks ``x`` transitively depends on, so the next
    element of the chain is the remaining milestone that has no other remaining
    milestone among its own ancestors.
    """
    remaining = list(milestones)
    chain = []
    while remaining:
        roots = [item for item in remaining
                 if not any(other["subtask_id"] in reachable[item["subtask_id"]]
                            for other in remaining if other is not item)]
        if len(roots) != 1:
            return None
        chain.append(roots[0])
        remaining.remove(roots[0])
    for earlier, later in zip(chain, chain[1:]):
        if earlier["subtask_id"] not in reachable[later["subtask_id"]]:
            return None
    return chain


def _terminal_role_ids(decomposition):
    terminal = []
    for constraint in decomposition.get("goal_state") or []:
        if not isinstance(constraint, dict):
            continue
        if constraint.get("subject_role") == "robot" and constraint.get("relation") == "near":
            if isinstance(constraint.get("object_role"), str):
                terminal.append(constraint["object_role"])
    return terminal


def _consistency_gate_exact(context, alignment, node_ids=None):
    """Compare the locked prebinding with the downstream alignment, per role.

    Raises :class:`PrebindingMismatch` when the comparison fails or cannot be
    established. A correspondence that cannot be verified is a failure, never a
    silent pass.
    """
    prebound = context.get("prebound_roles") or []
    expected = {role["role_id"]: role for role in prebound}
    decomposition = (alignment or {}).get("task_decomposition") or {}
    bindings = (alignment or {}).get("context_bindings")
    report = {
        "gate_version": GATE_VERSION,
        "step_phrase_revision": STEP_PHRASE_REVISION,
        "enforced": True,
        "task_class": context.get("task_class"),
        "comparison_basis": "role_id preserved across stages, plus per-role lexical consistency "
                            "between the locked role text/labels and the downstream role "
                            "description. Lexical overlap is not an independent semantic "
                            "verification. The union of bound ids is never compared.",
        "expected_roles": sorted(expected),
        "downstream_roles": [],
        "role_checks": [],
        "failure_codes": [],
        "requires_subgraph_expansion": False,
        "ordered_visit_check": {},
        "passed": False,
    }
    if context.get("task_class") != SUPPORTED_TASK_CLASS:
        report["failure_codes"].append("unsupported_task_class_for_gate")
        raise PrebindingMismatch(report)
    if not isinstance(bindings, dict) or not bindings:
        report["failure_codes"].append("missing_downstream_context_bindings")
        raise PrebindingMismatch(report)
    downstream = _decomposition_roles(decomposition)
    report["downstream_roles"] = sorted(downstream)
    missing = sorted(set(expected) - set(downstream))
    invented = sorted(set(downstream) - set(expected))
    unexpected_bindings = sorted(set(bindings) - set(expected))
    if missing:
        report["failure_codes"].append("prebound_role_missing_downstream")
        report["missing_role_ids"] = missing
    if invented:
        report["failure_codes"].append("unexpected_new_downstream_role")
        report["unexpected_role_ids"] = invented
    if unexpected_bindings:
        # An extra binding key could smuggle in an object no declared role asked for.
        report["failure_codes"].append("unexpected_context_binding_key")
        report["unexpected_binding_keys"] = unexpected_bindings
    expected_objects = {role_id: role.get("object_id") for role_id, role in expected.items()
                        if role.get("object_id") is not None}
    owner_of_object = {}
    for role_id, object_id in expected_objects.items():
        owner_of_object.setdefault(object_id, []).append(role_id)
    for role_id in sorted(expected):
        role = expected[role_id]
        check = {"role_id": role_id, "role_text": role.get("role_text"),
                 "expected_object_id": role.get("object_id"),
                 "expected_labels": role.get("expected_labels"),
                 "downstream_present": role_id in downstream,
                 "lexical_overlap": [], "actual_element_ids": None, "status": "unknown"}
        if role_id not in downstream:
            check["status"] = "missing_downstream"
            report["role_checks"].append(check)
            continue
        shared = sorted(downstream[role_id]["terms"]
                        & (tokens(role.get("role_text")) | tokens(role.get("expected_labels"))))
        check["lexical_overlap"] = shared
        if not shared:
            check["status"] = "lexical_consistency_unverifiable"
            report["failure_codes"].append("role_lexical_consistency_unverifiable")
            report["role_checks"].append(check)
            continue
        bound = bindings.get(role_id)
        element_ids = bound.get("element_ids") if isinstance(bound, dict) else None
        check["actual_element_ids"] = element_ids
        if not isinstance(element_ids, list) or not element_ids:
            check["status"] = "downstream_role_unbound"
            report["failure_codes"].append("downstream_role_unbound")
            report["role_checks"].append(check)
            continue
        if node_ids is not None and not set(element_ids) <= set(node_ids):
            check["status"] = "bound_object_outside_supplied_subgraph"
            report["failure_codes"].append("bound_object_outside_supplied_subgraph")
            report["requires_subgraph_expansion"] = True
            report["role_checks"].append(check)
            continue
        if role.get("object_id") is None:
            check["status"] = "prebinding_did_not_bind_this_role"
            report["failure_codes"].append("prebinding_role_not_bound")
            report["role_checks"].append(check)
            continue
        if set(element_ids) == {role["object_id"]}:
            check["status"] = "match"
        else:
            check["status"] = "object_mismatch"
            report["failure_codes"].append("role_object_mismatch")
            swapped = sorted({other for element_id in element_ids
                              for other in owner_of_object.get(element_id, [])
                              if other != role_id})
            if swapped:
                check["possible_role_swap_with"] = swapped
                report["failure_codes"].append("possible_role_swap")
        report["role_checks"].append(check)
    report["ordered_visit_check"] = _ordered_visit_check(context, decomposition, report)
    if any(_step_kind(step) in ("manipulation", "mixed")
           for step in decomposition.get("subtasks") or [] if isinstance(step, dict)):
        report["failure_codes"].append("affirmative_manipulation_in_navigation_task")
    report["failure_codes"] = sorted(set(report["failure_codes"]))
    report["passed"] = not report["failure_codes"]
    if not report["passed"]:
        raise PrebindingMismatch(report)
    return report


def _identity_words(text, object_id):
    """Bounded lexical identity, not synonym matching. Preserve every attribute/number."""
    if not isinstance(text, str):
        raise ValueError("role_identity_text_missing")
    suffix = re.search(r"\s*\(scene (?:instance|element(?: id)?) ([0-9]+)\)$", text, re.I)
    if suffix:
        if int(suffix.group(1)) != object_id:
            raise ValueError("role_identity_explicit_id_conflict")
        text = text[:suffix.start()]
    return tuple(word for word in text.lower().split() if word != "the")


def _role_occurrences(decomposition):
    pools = [decomposition.get(key, []) for key in
             ("task_roles", "object_requirements", "constraint_roles")]
    pools += [step.get("required_roles", []) for step in decomposition.get("subtasks", [])]
    occurrences = {}
    for pool in pools:
        if not isinstance(pool, list):
            raise ValueError("role_occurrences_malformed")
        seen = set()
        for role in pool:
            if not isinstance(role, dict) or not isinstance(role.get("role_id"), str):
                raise ValueError("role_occurrences_malformed")
            if role["role_id"] in seen:
                raise ValueError("duplicate_role_definition")
            seen.add(role["role_id"])
            occurrences.setdefault(role["role_id"], []).append(role)
    return occurrences


def _role_correspondence(context, alignment, node_ids):
    """One-to-one source identities AND exact role semantics, never an ID-set shortcut."""
    roles = context.get("prebound_roles", [])
    expected = {row["role_id"]: row for row in roles}
    if not expected or len(expected) != len(roles):
        raise ValueError("locked_roles_missing_or_duplicate")
    decomposition = alignment.get("task_decomposition") or {}
    actual = _role_occurrences(decomposition)
    bindings = alignment.get("context_bindings") or {}
    if set(actual) != set(expected) or set(bindings) != set(expected):
        raise ValueError("role_or_binding_keys_differ")
    owners = {}
    for role_id, row in expected.items():
        oid = row.get("object_id")
        if row.get("status") != "bound" or type(oid) is not int:
            raise ValueError("locked_role_not_bound")
        if oid in owners:
            raise ValueError("same_object_has_multiple_roles")
        owners[oid] = role_id
    mapping = {}
    for downstream_id, occurrences in actual.items():
        entry = bindings[downstream_id]
        ids = entry.get("element_ids") if isinstance(entry, dict) else None
        if (not isinstance(ids, list) or len(ids) != 1 or type(ids[0]) is not int
                or ids[0] not in owners):
            raise ValueError("downstream_identity_not_single_locked_object")
        oid = ids[0]
        if node_ids is not None and oid not in node_ids:
            raise ValueError("bound_object_outside_supplied_subgraph")
        locked_id = owners[oid]
        wanted = _identity_words(expected[locked_id].get("role_text"), oid)
        if not wanted:
            raise ValueError("locked_role_description_empty")
        for role in occurrences:
            if type(role.get("quantity")) is not int or role["quantity"] != 1 \
                    or role.get("required") is not True:
                raise ValueError("role_quantity_or_required_conflict")
            texts = [role.get("name"), role.get("semantic_query_en")]
            if "semantic_queries_en" in role:
                queries = role["semantic_queries_en"]
                if not isinstance(queries, list) or len(queries) != 1:
                    raise ValueError("role_queries_not_single")
                texts += queries
            if any(_identity_words(text, oid) != wanted for text in texts):
                raise ValueError("role_semantics_not_exact_under_bounded_article_rule")
        mapping[downstream_id] = locked_id
    if len(set(mapping.values())) != len(expected):
        raise ValueError("role_correspondence_not_bijective")
    return mapping


def consistency_gate(context, alignment, node_ids=None):
    """Strict by default; the explicit history profile may prove a derived correspondence."""
    try:
        return _consistency_gate_exact(context, alignment, node_ids)
    except PrebindingMismatch as exc:
        original = exc.report
        if context.get("role_identity_policy") != ROLE_IDENTITY_POLICY:
            raise
    try:
        mapping = _role_correspondence(context, alignment, node_ids)
        if all(left == right for left, right in mapping.items()):
            raise ValueError("identity_mapping_cannot_repair_failed_gate")
        inverse = {locked: downstream for downstream, locked in mapping.items()}
        comparison = copy.deepcopy(context)
        for row in comparison["prebound_roles"]:
            row["role_id"] = inverse[row["role_id"]]
        comparison["ordered_visit_role_ids"] = [
            inverse[rid] for rid in context["ordered_visit_role_ids"]]
        terminal = context.get("terminal_role_id")
        if terminal is None or terminal != context["ordered_visit_role_ids"][-1]:
            raise ValueError("locked_terminal_not_last_visit")
        comparison["terminal_role_id"] = inverse[terminal]
        # A permutation cannot exploit terminal_required=False to hide a conflicting goal.
        comparison["terminal_robot_location_required"] = True
        report = _consistency_gate_exact(comparison, alignment, node_ids)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        original["role_correspondence"] = {
            "policy": ROLE_IDENTITY_POLICY, "accepted": False,
            "reason": (exc.report["failure_codes"] if isinstance(exc, PrebindingMismatch)
                       else str(exc)), "raw_output_rewritten": False}
        raise PrebindingMismatch(original) from exc
    report["role_correspondence"] = {
        "policy": ROLE_IDENTITY_POLICY, "accepted": True,
        "downstream_to_locked": mapping,
        "locked_visit_role_ids": list(context["ordered_visit_role_ids"]),
        "locked_terminal_role_id": terminal,
        "original_gate_failure_codes": original["failure_codes"],
        "comparison_scope": "derived_context_only; downstream role namespace stays unchanged",
        "raw_output_rewritten": False, "binding_objects_changed": False,
        "independent_semantic_truth_verified": False}
    return report


def _ordered_visit_check(context, decomposition, report):
    """Verify the visit order through declared dependencies, never through list order."""
    visit_role_ids = list(context.get("ordered_visit_role_ids") or [])
    terminal_role_id = context.get("terminal_role_id")
    check = {
        "expected_visit_role_ids": visit_role_ids,
        "expected_terminal_role_id": terminal_role_id,
        "order_basis": "declared subtask dependency reachability between unambiguous "
                       "single-target navigation milestones",
        "step_phrase_revision": STEP_PHRASE_REVISION,
        "order_phrase_scope": "A step counts as navigation only from a whitelisted action "
                              "phrase at the head of one of its clauses, including its "
                              "inflected forms and the bounded terminal framings "
                              "'finish/end/conclude by <navigation phrase>' and "
                              "'finish/end/conclude at <place>'. A framing word followed by "
                              "an unknown or manipulation verb carries no navigation "
                              "evidence and leaves the order unestablished.",
        "order_scope_limit": "Role references inside a multi-role step are never read as a visit "
                             "order; an unestablished order is reported, not guessed.",
        "revisit_policy": "Every revisit must appear as its own milestone; repeated visits are "
                          "never collapsed.",
        "terminal_robot_location_required": context.get("terminal_robot_location_required"),
        "status": "not_applicable",
        "explicit_prohibitions_retained": [
            {"subtask_id": step.get("subtask_id"), "field": field, **span}
            for step in decomposition.get("subtasks") or [] if isinstance(step, dict)
            for field in ("description", "name", "summary", "action")
            for span in explicit_prohibitions(step.get(field))],
    }
    if not visit_role_ids:
        return check
    reachable, dependency_error = _dependency_reachability(decomposition)
    if reachable is None:
        check["status"] = "order_unverified"
        check["dependency_error"] = dependency_error
        report["failure_codes"].append("ordered_visit_order_unverified")
        return check
    milestones = _navigation_milestones(decomposition, visit_role_ids)
    if milestones is None:
        check["status"] = "order_unverified"
        check["reason"] = "no unambiguous single-target navigation milestone sequence could be " \
                          "identified, so the visit order is not established by the decomposition"
        report["failure_codes"].append("ordered_visit_order_unverified")
        return check
    check["milestones"] = milestones
    if len(milestones) != len(visit_role_ids):
        check["status"] = "milestone_count_mismatch"
        report["failure_codes"].append("ordered_visit_milestone_count_mismatch")
        return check
    chain = _milestone_chain(milestones, reachable)
    if chain is None:
        check["status"] = "order_unverified"
        check["reason"] = "the navigation milestones do not form a single dependency chain, so " \
                          "their order is not established by the decomposition"
        report["failure_codes"].append("ordered_visit_order_unverified")
        return check
    actual = [item["role_id"] for item in chain]
    check["downstream_chain"] = actual
    if actual != visit_role_ids:
        check["status"] = "mismatch"
        report["failure_codes"].append("ordered_visit_mismatch")
        return check
    check["status"] = "match"
    terminal = _terminal_role_ids(decomposition)
    check["downstream_terminal_role_ids"] = terminal
    if context.get("terminal_robot_location_required") is True:
        if terminal_role_id is not None and terminal != [terminal_role_id]:
            check["terminal_status"] = "mismatch"
            report["failure_codes"].append("terminal_goal_mismatch")
        else:
            check["terminal_status"] = "match"
    else:
        # The binding contract did not request a terminal robot location, so no
        # terminal robot-near goal is expected or invented here.
        check["terminal_status"] = "not_required_by_binding_contract"
    return check
