"""Deterministic, explicitly synthetic responder for the three legacy model stages.

This is NOT a model and NOT a semantic resolver. It exists so the unchanged
request builders, typed codebooks, validators, decoders and plan assembly of the
three-stage pipeline can be exercised end to end without any network access.

Rules (exact and deliberately narrow):

* decomposition: every active ``object_tag`` from the scene file named by
  ``SGTP_SYNTHETIC_VOCABULARY`` that occurs verbatim (case-insensitive, whole words,
  longest label first, no overlaps) in the instruction becomes one ordered
  navigation visit. Distinct labels become roles in first-mention order; each visit
  depends on the previous one; the final goal is ``robot near <last visited role>``.
  Only navigation is produced. An instruction with no exact label raises an error,
  which the pipeline records as an incomplete run.
* object binding: a role is ``matched`` when exactly one supplied active node has an
  identical ``object_tag``, ``ambiguous`` (with candidates) when several do, and
  ``missing`` when none does. Captions, attributes and relations are ignored.
* skill matching: each subtask gets one ``navigate_to_object`` row on the bound
  instance of its role.

Selected with ``--backend synthetic`` (``scripted:sgtp.synthetic_backend:respond``).
Every response is marked with ``RESPONDER_ID``; no output of this module is model
evidence.
"""

import json
import os
from pathlib import Path
import re

VOCABULARY_ENV = "SGTP_SYNTHETIC_VOCABULARY"
RESPONDER_ID = "sgtp_synthetic_exact_label_responder_v1"
NAVIGATION_SKILL = "navigate_to_object"
PROCESS_CONTRACT = "process_constraints_v1"


class SyntheticResponderError(RuntimeError):
    """The synthetic rules cannot produce a response; nothing is guessed."""


def vocabulary(path=None):
    path = path or os.environ.get(VOCABULARY_ENV, "").strip()
    if not path:
        raise SyntheticResponderError(VOCABULARY_ENV + " must name a scene_graph.json file")
    nodes = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(nodes, dict):
        nodes = nodes.get("nodes")
    if not isinstance(nodes, list):
        raise SyntheticResponderError("Vocabulary file is not a node array")
    labels = {n["object_tag"].strip() for n in nodes
              if isinstance(n, dict) and n.get("active", True) is not False
              and isinstance(n.get("object_tag"), str) and n["object_tag"].strip()}
    return sorted(labels, key=lambda label: (-len(label), label))


def mentions(instruction, labels):
    """Ordered exact label mentions, longest label first, without overlaps."""
    text = instruction.casefold()
    taken, found = [], []
    for label in labels:
        pattern = re.compile(r"(?<![0-9a-z])" + re.escape(label.casefold()) + r"(?![0-9a-z])")
        for match in pattern.finditer(text):
            if any(match.start() < end and start < match.end() for start, end in taken):
                continue
            taken.append((match.start(), match.end()))
            found.append((match.start(), label))
    return [label for _, label in sorted(found)]


def visit_plan(instruction, labels=None):
    visits = mentions(instruction, vocabulary() if labels is None else labels)
    if not visits:
        raise SyntheticResponderError("No exact scene label occurs in the instruction; the "
                                      "synthetic responder does not guess targets")
    roles = []
    for label in visits:
        if label not in roles:
            roles.append(label)
    return visits, roles


def _issued(book, kind):
    return [entry["code"] for entry in book["entries"] if entry["kind"] == kind]


def _digits(book):
    match = re.fullmatch(r"typed([59])_ids_v1", str(book.get("schema_version")))
    if match is None:
        raise SyntheticResponderError("Unknown identifier allocation version")
    return match.group(1)


def decomposition(data):
    book = data["identifier_allocation"]
    visits, roles = visit_plan(data["instruction"])
    role_codes, task_codes = _issued(book, "role"), _issued(book, "subtask")
    goal_codes, robot = _issued(book, "constraint"), _issued(book, "executor")[0]
    if len(roles) > len(role_codes) or len(visits) > len(task_codes):
        raise SyntheticResponderError("Issued identifier capacity is insufficient")
    code_of = {label: role_codes[i] for i, label in enumerate(roles)}
    tasks = [[task_codes[i], "Navigate to the " + label + "."] for i, label in enumerate(visits)]
    return {
        "schema_version": "structural_typed" + _digits(book) + "_v1",
        "codebook_sha256": book["sha256"],
        "instruction": data["instruction"],
        "task_name": "Ordered navigation visits (synthetic responder)",
        "roles": [[code_of[label], label, label, 1, 1] for label in roles],
        "labels": [[code_of[label], label] for label in roles],
        "tasks": tasks,
        "task_roles": [[task_codes[i], code_of[label]] for i, label in enumerate(visits)],
        "dependencies": [[task_codes[i - 1], task_codes[i]] for i in range(1, len(visits))],
        "constraints": [[goal_codes[0], 2, robot, 3, code_of[visits[-1]], -1, 1]],
        "process_constraint_contract": PROCESS_CONTRACT,
        "process_constraints": [],
    }


def object_binding(data):
    book = data["object_binding_codebook"]
    scene_code = {entry["element_id"]: entry["code"] for entry in book["scene_codes"]}
    nodes = [n for n in data["nodes"] if n.get("active", True) is not False]
    decisions, matches, candidates, reasons = [], [], [], []
    for role in book["roles"]:
        code, name = role["role_code"], role["name"].strip().casefold()
        ids = sorted(n["id"] for n in nodes
                     if isinstance(n.get("object_tag"), str)
                     and n["object_tag"].strip().casefold() == name)
        if len(ids) == 1:
            decisions.append([code, 1])
            matches.append([code, scene_code[ids[0]]])
            reasons.append([code, "Synthetic rule: exactly one active node has this exact "
                                  "object_tag."])
        elif ids:
            decisions.append([code, 3])
            candidates.extend([code, scene_code[i]] for i in ids)
            reasons.append([code, "Synthetic rule: several active nodes share this exact "
                                  "object_tag; they are not distinguished."])
        else:
            decisions.append([code, 2])
            reasons.append([code, "Synthetic rule: no active node has this exact object_tag."])
    digits = "5" if "typed5" in str(book.get("schema_version")) else "9"
    return {"schema_version": "object_binding_typed" + digits + "_v1",
            "codebook_sha256": book["codebook_sha256"], "decisions": decisions,
            "matches": matches, "candidates": candidates, "reasons": reasons}


def skill_matrix(data):
    visits, roles = visit_plan(data["instruction"])
    skill = next((s for s in data["skills"] if s.get("skill_id") == NAVIGATION_SKILL), None)
    if skill is None:
        raise SyntheticResponderError(NAVIGATION_SKILL + " is not offered in this request")
    objects = {}
    for entry in data["object_codes"]:
        objects.setdefault(entry["role_id"], entry["code"])
    rows = []
    for row in data["subtasks"]:
        index = int(row["subtask_id"].rsplit("_", 1)[1]) - 1
        if not 0 <= index < len(visits):
            raise SyntheticResponderError("Subtask does not correspond to a parsed visit")
        role_id = "role_%03d" % (roles.index(visits[index]) + 1)
        if role_id not in objects:
            raise SyntheticResponderError("Role " + role_id + " has no bound instance")
        rows.append([row["code"], skill["code"], objects[role_id], 0])
    version = re.sub(r"_ids_v1$", "", str(data.get("identifier_context", {}).get("version")))
    return {"schema_version": "action_matrix_" + version + "_v1",
            "codebook_sha256": data["codebook_sha256"], "matrix": rows}


BUILDERS = {"decomposition": decomposition, "object_binding": object_binding,
            "skill_matching_matrix": skill_matrix}


def stage_of(data):
    if "identifier_allocation" in data:
        return "decomposition"
    if "object_binding_codebook" in data:
        return "object_binding"
    if "object_codes" in data:
        return "skill_matching_matrix"
    raise SyntheticResponderError("Unrecognised request; only the three typed stages are served")


def respond(stage, payload):
    """Return an OpenAI-style completion envelope for one already-built request."""
    data = json.loads(payload["messages"][1]["content"])
    inferred = stage_of(data)
    if stage not in ("unknown", inferred):
        raise SyntheticResponderError("Audit stage " + repr(stage) + " does not match request "
                                      + repr(inferred))
    document = BUILDERS[inferred](data)
    return {"id": "synthetic-" + inferred, "object": "chat.completion", "model": RESPONDER_ID,
            "synthetic_responder": True,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps(document)}}]}
