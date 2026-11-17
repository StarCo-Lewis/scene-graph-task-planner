"""Keep model-written evidence prose out of engineering JSON; keep it in an audit log.

An engineering JSON is the artefact downstream code and reviewers act on. Free text a
model wrote to justify a decision is not a decision field, so it is moved out of that
file into a separate, exclusively created audit log next to it. Nothing is truncated,
nothing is summarised, and this module never opens a raw request or response file;
whether such a file exists upstream is not something it can verify, and it never
claims so.

Scope is by field name only, never by a substring heuristic:

* Inside any dict, at any depth, a key in :data:`EVIDENCE_TEXT_KEYS` whose value is a
  string or ``None`` is removed. A string value becomes one log entry and the same
  dict gains an ``audit_log_refs`` list pointing at that entry. A dict or list under
  such a key is a program container, not model prose, and is searched recursively.
* A string under one of the known carrier keys (:data:`SERIALIZED_CARRIER_KEYS`, for
  example ``error``, ``detail`` or ``model_proposal``) that is, as a whole, a legal
  JSON object or array is parsed, stripped by the same rule and serialised back in
  place, so a proposal carried as text cannot smuggle its evidence through. Text that
  is not entirely JSON is left untouched, and keys such as ``instruction`` or
  ``clarification_question`` are never parsed (:data:`NEVER_PARSED_KEYS`).

Every delivery writes an audit log, also when no evidence text was present (a prompt
that no longer asks for prose), because the log also keeps the validation and
candidate-review records of that engineering JSON together with record_id, stage and
attempt. If the log cannot be written, the engineering JSON is still delivered, the
text is still not put back, and the view is marked unusable and incomplete with the
values it had before, so a missing audit trail can never pass silently as usable.
"""

import copy
import hashlib
import json
import re
from pathlib import Path

from . import text_budget

EVIDENCE_LOG_VERSION = "evidence_audit_log_v1_20261108"
EVIDENCE_TEXT_KEYS = ("evidence", "why_still_fits")
#: Keys whose string value may be a whole serialised JSON document from a known
#: carrier: an error or detail record, a raw or decoded proposal, a response body.
SERIALIZED_CARRIER_KEYS = (
    "error", "errors", "detail", "details", "partial_evidence", "stage_failure_evidence",
    "raw", "raw_response", "raw_text", "raw_document", "response", "response_body",
    "response_text", "content", "model_proposal", "proposal", "original_proposal",
    "document", "message", "refusal")
#: Task text and questions are never parsed or stripped, whatever they contain.
NEVER_PARSED_KEYS = (
    "instruction", "original_instruction", "local_instruction", "clarification_question",
    "question", "questions", "role_questions", "role_text", "target_phrase", "phrase",
    "parsed_phrases")
#: Validation and candidate-review records copied into the log for every delivery.
VALIDATION_RECORD_KEYS = (
    "raw_contract_validation", "original_contract_check", "candidate_review",
    "candidate_reviews", "candidate_review_records", "binding_validation", "validation",
    "consistency_gate", "gate_report", "local_contract_checks", "contract_check",
    "clarification_evidence", "failed_check", "local_failed_check", "stop")
REFS_KEY = "audit_log_refs"
AUDIT_KEY = "evidence_audit"
JSON_MARKER = "#serialized_json"
INCOMPLETE = "incomplete"
LOG_PREFIX = "evidence_audit"
MAX_NAME_SUFFIX = 50
#: Short, fixed-length file names: a long attempt (for example a whole relative path)
#: once produced names past the Windows path limit. The full stage and attempt stay
#: inside the log; the name carries only a short stage slug and a digest of both.
STAGE_SLUG_MAX_CHARS = 16
NAME_DIGEST_HEX_CHARS = 16
USABLE_KEYS = ("usable_as_plan", "usable")
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
_SCALARS = (str, int, float, bool, type(None))


class _Collector:
    def __init__(self, log_name):
        self.log_name = log_name
        self.entries = []
        self.removed = []
        self.reserialized = []


def _pointer(parts):
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _is_scalar_list(value):
    return isinstance(value, list) and all(isinstance(item, _SCALARS) for item in value)


def _context(stripped):
    """Scalar and scalar-list siblings: ids, status, region, question and the like."""
    return {key: copy.deepcopy(value) for key, value in stripped.items()
            if key not in (REFS_KEY, AUDIT_KEY)
            and (isinstance(value, _SCALARS) or _is_scalar_list(value))}


def parse_serialized(text):
    """The JSON object or array ``text`` is as a whole, or ``None``. Never raises."""
    if not isinstance(text, str):
        return None
    body = text.strip()
    if not body or body[0] not in "{[":
        return None
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, (dict, list)) else None


def _carrier_string(text, parts, collector):
    parsed = parse_serialized(text)
    if parsed is None or not contains_evidence_text(parsed):
        return text
    stripped = _strip(parsed, parts + [JSON_MARKER], collector)
    collector.reserialized.append(_pointer(parts))
    return json.dumps(stripped, ensure_ascii=False)


def _strip_value(key, item, parts, collector):
    if key in NEVER_PARSED_KEYS:
        # Task text and questions are never parsed; a container under such a key is
        # still searched for evidence fields by the ordinary rule.
        return _strip(item, parts, collector)
    if key in SERIALIZED_CARRIER_KEYS:
        if isinstance(item, str):
            return _carrier_string(item, parts, collector)
        if isinstance(item, list):
            return [_carrier_string(element, parts + [index], collector)
                    if isinstance(element, str)
                    else _strip(element, parts + [index], collector)
                    for index, element in enumerate(item)]
    return _strip(item, parts, collector)


def _strip(value, parts, collector):
    if isinstance(value, list):
        return [_strip(item, parts + [index], collector) for index, item in enumerate(value)]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    result = {}
    taken = []
    for key, item in value.items():
        if key in EVIDENCE_TEXT_KEYS and isinstance(item, (str, type(None))):
            taken.append((key, item))
            continue
        result[key] = _strip_value(key, item, parts + [key], collector)
    if not taken:
        return result
    existing = result.get(REFS_KEY)
    refs = list(existing) if isinstance(existing, list) else []
    context = _context(result)
    for key, item in taken:
        pointer = _pointer(parts + [key])
        collector.removed.append(pointer)
        if item is None:
            refs.append({"field": key, "value_was_null": True, "entry_id": None})
            continue
        entry_id = "entry_%04d" % (len(collector.entries) + 1)
        digest = hashlib.sha256(item.encode("utf-8")).hexdigest()
        collector.entries.append({
            "entry_id": entry_id, "json_pointer": pointer, "field": key, "text": item,
            "sha256": digest, "characters": len(item),
            "words": text_budget.count_words(item),
            "word_count_is_a_measurement_not_a_limit": True,
            "inside_serialized_json_carrier": JSON_MARKER in parts, "context": context})
        refs.append({"field": key, "entry_id": entry_id, "sha256": digest,
                     "audit_log_file": collector.log_name})
    result[REFS_KEY] = refs
    return result


def split_evidence(document, log_name):
    """Return ``(stripped_copy, collector)``. ``document`` itself is never changed."""
    collector = _Collector(log_name)
    return _strip(document, [], collector), collector


def contains_evidence_text(document):
    """Whether an evidence text field with a string value remains anywhere.

    Also looks inside whole-JSON strings under the known carrier keys, which is the
    same scope :func:`split_evidence` strips.
    """
    if isinstance(document, list):
        return any(contains_evidence_text(item) for item in document)
    if not isinstance(document, dict):
        return False
    for key, value in document.items():
        if key in EVIDENCE_TEXT_KEYS and isinstance(value, str):
            return True
        if key in SERIALIZED_CARRIER_KEYS and key not in NEVER_PARSED_KEYS:
            items = value if isinstance(value, list) else [value]
            for item in items:
                parsed = parse_serialized(item)
                if parsed is not None and contains_evidence_text(parsed):
                    return True
        if contains_evidence_text(value):
            return True
    return False


def _validation_records(value, parts, found):
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validation_records(item, parts + [index], found)
        return found
    if not isinstance(value, dict):
        return found
    for key, item in value.items():
        if key in VALIDATION_RECORD_KEYS and item is not None:
            found.append({"json_pointer": _pointer(parts + [key]),
                          "record": copy.deepcopy(item)})
        elif key != AUDIT_KEY:
            _validation_records(item, parts + [key], found)
    return found


def _safe(text):
    return _UNSAFE.sub("_", str(text)) or "none"


def name_digest(stage, attempt):
    """Stable short digest of the exact stage and attempt strings (length-prefixed)."""
    stage_text, attempt_text = str(stage), str(attempt)
    key = "%d:%s|%d:%s" % (len(stage_text), stage_text, len(attempt_text), attempt_text)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:NAME_DIGEST_HEX_CHARS]


def log_names(stage, attempt):
    """Candidate names, at most about 60 characters whatever stage and attempt are.

    Collisions with an existing file move to the next numeric suffix; the file is
    always created exclusively, so nothing is ever overwritten.
    """
    slug = _safe(stage)[:STAGE_SLUG_MAX_CHARS]
    base = LOG_PREFIX + "__" + slug + "__" + name_digest(stage, attempt)
    yield base + ".json"
    for number in range(2, MAX_NAME_SUFFIX + 1):
        yield base + "__" + str(number) + ".json"


def _document_summary(document):
    if not isinstance(document, dict):
        return {}
    return {key: copy.deepcopy(value) for key, value in document.items()
            if isinstance(value, _SCALARS) or _is_scalar_list(value)}


def resolve_record_id(document, record_id=None):
    """``(record_id, source)``: the caller's value first, then the document's own."""
    if record_id is not None:
        return record_id, "caller"
    if isinstance(document, dict):
        if document.get("record_id") is not None:
            return document["record_id"], "document.record_id"
        task = document.get("task")
        if isinstance(task, dict) and task.get("record_id") is not None:
            return task["record_id"], "document.task.record_id"
    return None, "missing"


def _mark_failed(view, error):
    view[AUDIT_KEY]["audit_logging_failed"] = True
    view[AUDIT_KEY]["error"] = error
    # This function cannot know whether an upstream raw file exists, so it states
    # only what it did itself: it modified no raw file and verified no persistence.
    view[AUDIT_KEY]["raw_response_files_not_modified_by_this_function"] = True
    view[AUDIT_KEY]["full_text_persistence_unverified"] = True
    for key in USABLE_KEYS:
        if key in view:
            view[key + "_before_audit_failure"] = view[key]
            view[key] = False
    if "usable_as_plan" not in view:
        view["usable_as_plan_before_audit_failure"] = None
        view["usable_as_plan"] = False
    view["artifact_completeness_before_audit_failure"] = view.get("artifact_completeness")
    view["artifact_completeness"] = INCOMPLETE
    view["audit_logging_failed"] = True
    return view


def _fallback(document, error, record_id):
    """Used only if stripping itself failed. Carries no model text at all."""
    source = document if isinstance(document, dict) else {}
    view = {"plan": None, "record_id": record_id,
            "usable_as_plan": False, "artifact_completeness": INCOMPLETE,
            "audit_logging_failed": True,
            AUDIT_KEY: {"version": EVIDENCE_LOG_VERSION, "audit_log_file": None,
                        "entry_count": 0, "audit_log_written": False,
                        "audit_logging_failed": True, "error": error,
                        "evidence_text_removed_from_engineering_json": True,
                        "engineering_json_replaced_by_minimal_envelope": True,
                        "raw_response_files_not_modified_by_this_function": True,
                        "full_text_persistence_unverified": True}}
    for key in ("usable_as_plan", "usable", "artifact_completeness"):
        if key in source and isinstance(source[key], _SCALARS):
            view[key + "_before_audit_failure"] = source[key]
    return view


def engineering_view(document, directory, stage, attempt=None, record_id=None,
                     engineering_json_name="engineering_plan.json", record_id_source=None):
    """The engineering JSON to write, with evidence prose moved to a new audit log.

    Never raises. Pass ``record_id`` explicitly whenever the document may not carry
    one, so every stage log can be joined to its task. The log is created exclusively
    and an existing file is never overwritten: a later numeric suffix is used instead.
    ``record_id_source`` labels a caller-supplied id that is an alias, for example a
    ``task_id`` used where no ``record_id`` exists; it defaults to ``"caller"``.
    """
    resolved, source = None, "missing"
    try:
        resolved, source = resolve_record_id(document, record_id)
        if record_id is not None and record_id_source:
            source = str(record_id_source)
        directory = Path(directory)
        if attempt is None:
            attempt = directory.name
        last_error = None
        view = None
        for name in log_names(stage, attempt):
            view, collector = split_evidence(document, name)
            if not isinstance(view, dict):
                return view
            audit = {"version": EVIDENCE_LOG_VERSION, "audit_log_file": None,
                     "record_id": resolved, "record_id_source": source, "stage": stage,
                     "attempt": attempt, "entry_count": len(collector.entries),
                     "audit_log_written": False, "audit_logging_failed": False,
                     "error": None,
                     "evidence_text_removed_from_engineering_json": True,
                     "fields_removed": collector.removed,
                     "serialized_json_carriers_rewritten": collector.reserialized,
                     "removal_rule": "dict keys " + ", ".join(EVIDENCE_TEXT_KEYS)
                                     + " with a string or null value, at any depth, "
                                       "including inside whole-JSON strings under known "
                                       "carrier keys"}
            view[AUDIT_KEY] = audit
            log = {"version": EVIDENCE_LOG_VERSION, "record_id": resolved,
                   "record_id_source": source, "stage": stage, "attempt": attempt,
                   "log_file_name": name,
                   "log_file_name_rule": "evidence_audit__<stage slug>__<sha256 of "
                                         "stage and attempt, 16 hex>[__n].json; the full "
                                         "stage and attempt are the fields above",
                   "engineering_json_name": engineering_json_name,
                   "engineering_document_summary": _document_summary(document),
                   "engineering_document_summary_note":
                       "copied delivered fields only; usable_as_plan or a complete "
                       "artifact is a structural outcome, not semantic verification",
                   "semantic_correctness_verified_by_this_log": False,
                   "validation_records": _validation_records(view, [], []),
                   "entries": collector.entries,
                   "evidence_text_present": bool(collector.entries),
                   "text_is_complete_and_untruncated": True,
                   "raw_response_files_not_modified_by_this_function": True}
            path = directory / name
            try:
                payload = json.dumps(log, ensure_ascii=False, indent=2) + "\n"
                with open(path, "x", encoding="utf-8") as handle:
                    handle.write(payload)
            except FileExistsError:
                last_error = "audit log name already taken: " + name
                continue
            except Exception as exc:  # noqa: BLE001 - the plan must still be delivered
                return _mark_failed(view, type(exc).__name__ + ": " + str(exc))
            audit["audit_log_file"] = name
            audit["audit_log_written"] = True
            return view
        return _mark_failed(view, last_error or "no free audit log name")
    except Exception as exc:  # noqa: BLE001 - never block delivery of the plan
        return _fallback(document, type(exc).__name__ + ": " + str(exc), resolved)
