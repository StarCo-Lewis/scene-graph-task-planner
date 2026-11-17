"""Release-only, provider-neutral backend switch for the two legacy network exits.

Scene Graph Task Planner keeps the original request builders, prompts, validators and
urllib transport. This module is consulted at the top of ``llm_transport.request_json``
and ``match_skills.request_json_with_retry`` and decides what happens to an
already-built request:

``offline`` (default)
    No request is sent. ``BackendRefused`` is raised, which the unchanged pipeline turns
    into an explicitly incomplete engineering envelope (``plan`` is null).
``recorded:<file>``
    Serve stored completion envelopes per stage, in order. No model is invoked.
    Every stored response is bound to the identity of the request it answered
    (``request_sha256`` of the exact request body bytes, see ``IDENTITY_RULE``). A
    response is served only when the request being sent now has exactly those bytes,
    so a changed
    instruction, scene, context, catalogue, schema, model name or any other request
    field stops the run instead of reusing an answer. The plan scope
    (``SGTP_PLAN_SCOPE``) and catalogue profile must also equal the recorded ones.
    Recordings without request identities (format v1) are refused, never guessed.
``scripted:<module>:<callable>``
    Call ``callable(stage, payload)`` to build the completion envelope (test doubles).
    No model is invoked.
``live``
    Use the original urllib path. Requires ``SGTP_ACKNOWLEDGE_LIVE_API=1`` as well;
    there is no automatic fallback from any other backend to ``live``.

The selection is read from the environment so that the alignment subprocess started by
the worker inherits it. Every decision is appended to ``SGTP_BACKEND_LOG`` when set.
"""

import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import threading

BACKEND_ENV = "SGTP_LLM_BACKEND"
ACK_ENV = "SGTP_ACKNOWLEDGE_LIVE_API"
LOG_ENV = "SGTP_BACKEND_LOG"
SCOPE_ENV = "SGTP_PLAN_SCOPE"
PROFILE_ENV = "SGTP_CATALOGUE_PROFILE"
RECORDED_FORMAT = "sgtp_recorded_responses_v2"
IDENTITY_RULE = (
    "request_sha256 is the SHA-256 of the exact request body bytes that would have been "
    "sent. Nothing is parsed, normalised, removed or reordered, so every byte of every "
    "field (instruction, scene and context, catalogue skills, schemas, codebooks, model "
    "name, sampling parameters) is part of the identity. Any difference refuses replay.")
KINDS = ("offline", "recorded", "scripted", "live")
LIVE = object()
_LOCK = threading.Lock()
_CURSORS = {}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class BackendRefused(RuntimeError):
    """No request was sent and no response is available from the selected backend."""


def selected():
    value = os.environ.get(BACKEND_ENV, "").strip() or "offline"
    kind, _, argument = value.partition(":")
    if kind not in KINDS:
        raise BackendRefused("Unknown model backend " + repr(kind) + "; no request made")
    if kind in ("recorded", "scripted") and not argument:
        raise BackendRefused(kind + " backend requires an argument; no request made")
    if kind in ("offline", "live") and argument:
        raise BackendRefused(kind + " backend takes no argument; no request made")
    return kind, argument


def _sha(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value):
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def request_identity(body):
    """Identity of a request body: SHA-256 of its exact bytes (see IDENTITY_RULE)."""
    return {"request_sha256": _sha(body or b"")}


def _profile():
    return os.environ.get(PROFILE_ENV, "").strip() or None


def _log(entry):
    path = os.environ.get(LOG_ENV, "").strip()
    if not path:
        return
    entry = dict(entry, pid=os.getpid(), plan_scope=os.environ.get(SCOPE_ENV, "").strip() or None)
    with _LOCK:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")
            stream.flush()


def _audit_write(audit, suffix, data):
    if audit is not None and hasattr(audit, "write"):
        audit.write(suffix, data)


def _recorded(path, stage, identity):
    """Next recorded response for ``stage``, only if it answered this exact request."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("format") != RECORDED_FORMAT or not isinstance(document.get("responses"), dict):
        raise BackendRefused("Recorded response file has an unknown or older format without "
                             "request identities (expected " + RECORDED_FORMAT + "); record "
                             "the run again; no request made")
    recorded_scope = document.get("plan_scope")
    current_scope = os.environ.get(SCOPE_ENV, "").strip() or None
    if recorded_scope != current_scope:
        raise BackendRefused("Recorded responses were made under plan scope "
                             + repr(recorded_scope) + " but the current scope is "
                             + repr(current_scope) + "; no request made")
    if document.get("catalogue_profile") != _profile():
        raise BackendRefused("Recorded responses were made under catalogue profile "
                             + repr(document.get("catalogue_profile")) + " but the current "
                             "profile is " + repr(_profile()) + "; no request made")
    queue = document["responses"].get(stage)
    with _LOCK:
        index = _CURSORS.get((path, stage), 0)
        if not isinstance(queue, list) or index >= len(queue):
            raise BackendRefused("No recorded response left for stage " + repr(stage)
                                 + "; no request made")
        entry = queue[index]
        expected = entry.get("request_sha256") if isinstance(entry, dict) else None
        if not _is_sha256(expected) or not isinstance(entry.get("response"), dict):
            raise BackendRefused("Recorded response " + str(index) + " for stage " + repr(stage)
                                 + " is not bound to a request identity; no request made")
        if identity.get("request_sha256") != expected:
            raise BackendRefused(
                "The current " + repr(stage) + " request differs from the request this "
                "recorded response answered (instruction, scene, context, catalogue, schema "
                "or another request field changed); the recorded answer is not reused; "
                "no request made")
        _CURSORS[(path, stage)] = index + 1
    return entry["response"], index, expected


def _scripted(spec, stage, payload):
    module_name, _, attribute = spec.rpartition(":")
    if not module_name or not attribute:
        raise BackendRefused("scripted backend expects module:callable; no request made")
    function = getattr(importlib.import_module(module_name), attribute)
    return function(stage, payload)


def route(request, audit=None):
    """Return ``LIVE`` for the original transport, or a parsed completion envelope."""
    kind, argument = selected()
    stage = getattr(audit, "stage", None) or "unknown"
    body = request.data or b""
    base = dict({"backend": kind, "stage": stage, "catalogue_profile": _profile()},
                **request_identity(body))
    if kind == "live":
        if os.environ.get(ACK_ENV, "").strip() != "1":
            _log(dict(base, decision="refused_live_without_acknowledgement", model_invoked=False))
            _audit_write(audit, "release_backend_refused",
                         {"event": "release_backend_refused", "backend": kind,
                          "reason": "live_api_not_acknowledged", "model_invoked": False})
            raise BackendRefused("Live API use was not acknowledged (" + ACK_ENV
                                 + "=1); no request made")
        _log(dict(base, decision="live_request_delegated_to_original_transport",
                  model_invoked="attempted"))
        return LIVE
    if kind == "offline":
        _log(dict(base, decision="refused_offline_default", model_invoked=False))
        _audit_write(audit, "release_backend_refused",
                     {"event": "release_backend_refused", "backend": kind,
                      "reason": "offline_default_no_model", "model_invoked": False})
        raise BackendRefused("Offline backend (default): no model request was made for stage "
                             + repr(stage) + ". Select recorded:, scripted: or live explicitly.")
    payload = json.loads(body.decode("utf-8")) if body else {}
    index, matched = None, None
    if kind == "recorded":
        try:
            response, index, matched = _recorded(argument, stage, base)
        except BackendRefused as exc:
            _log(dict(base, decision="refused_recorded_replay", model_invoked=False,
                      reason=str(exc)))
            _audit_write(audit, "release_backend_refused",
                         {"event": "release_backend_refused", "backend": kind,
                          "reason": str(exc), "model_invoked": False})
            raise
    else:
        response = _scripted(argument, stage, payload)
    raw = json.dumps(response, ensure_ascii=False)
    identity_matched = (matched is not None) if kind == "recorded" else None
    _log(dict(base, decision="served_without_model", model_invoked=False,
              recorded_index=index, recorded_request_identity_matched=identity_matched,
              response_sha256=_sha(raw), response=response))
    _audit_write(audit, "release_backend_response",
                 {"event": "release_backend_response", "backend": kind, "model_invoked": False,
                  "http_request_sent": False, "response_body": raw,
                  "response_sha256": _sha(raw)})
    return json.loads(raw)


def recorded_from_log(log_path):
    """Turn a backend log into a recorded-response document (stage order preserved).

    Each response keeps the identity of the request it answered. A log line without
    request identities (an older log) is refused rather than recorded unbound.
    """
    responses, scopes, profiles = {}, set(), set()
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("decision") == "served_without_model":
            exact = entry.get("request_sha256")
            if not _is_sha256(exact):
                raise BackendRefused("Backend log entry for stage " + repr(entry.get("stage"))
                                     + " carries no request identity; record a new run")
            responses.setdefault(entry["stage"], []).append(
                {"request_sha256": exact, "response": entry["response"]})
            scopes.add(entry.get("plan_scope"))
            profiles.add(entry.get("catalogue_profile"))
    if not responses:
        raise BackendRefused("Backend log contains no served responses to record")
    if len(scopes) > 1 or len(profiles) > 1:
        raise BackendRefused("Backend log mixes plan scopes or catalogue profiles; cannot "
                             "form one recording")
    scope = scopes.pop() if scopes else None
    if scope is None:
        raise BackendRefused("Responses were served under a random plan scope; set "
                             + SCOPE_ENV + " when recording a run that should be replayed")
    profile = profiles.pop()
    if profile is None:
        raise BackendRefused("Backend log does not name a catalogue profile; record a new run")
    return {"format": RECORDED_FORMAT, "plan_scope": scope, "catalogue_profile": profile,
            "identity_rule": IDENTITY_RULE, "responses": responses,
            "note": "Synthetic or recorded completion envelopes; replay is not a model run."}
