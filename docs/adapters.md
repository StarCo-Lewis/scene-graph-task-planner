# Changes to the copied code

All code under `embodied_pipeline/` and `vendor/legacy_engines/` comes from the authors'
research workspace. 48 copied files are byte-identical. The six files below are adapted;
each change is marked `Release-only adapter` in the source. `RELEASE_PROVENANCE.json`
lists both hashes for every copied file.

Path mapping: `embodied_pipeline/**` keeps its workspace path, and
`vendor/legacy_engines/<engine>/<file>` comes from the workspace `outputs/<engine>/<file>`.

| File | Change |
|---|---|
| `embodied_pipeline/_legacy.py` | Loads engines from `vendor/legacy_engines/` (through `_release.ENGINE_ROOT`) instead of the workspace `outputs/`, and only the three planning engines plus `release_adapters` |
| `embodied_pipeline/orchestration/__init__.py` | Exports nothing eagerly. The original imported proposal and feedback runners that depend on the excluded proxy executor |
| `embodied_pipeline/orchestration/task_collection_worker.py` | Library and offered skills come from the active catalogue profile; under the formal profile the contract gets `release_catalogue_scope`; the hashed source-file list points at the vendored paths |
| `vendor/legacy_engines/object_alignment_v1/llm_transport.py` | Calls `release_backend.route` before the original urllib request (stages 1 and 2) |
| `vendor/legacy_engines/skill_matching_v1/match_skills.py` | Same backend switch before the original request (stage 3); a refusal becomes the module's own `PlanError` |
| `vendor/legacy_engines/llm_audit_v1/typed_matrix_ids.py` | `allocation()` uses `SGTP_PLAN_SCOPE` as the plan id when it is set; otherwise the original random `uuid4` |

Prompts, validators, codebook rules, decoders and skill definitions are otherwise
unchanged.

## Release-only files

| File | Purpose |
|---|---|
| `embodied_pipeline/_release.py` | Release root, engine root and catalogue profiles |
| `vendor/legacy_engines/release_adapters/release_backend.py` | Backend switch: offline, recorded, scripted, live. Recorded responses are bound to the exact request body (`request_sha256`) and served only for an identical request; any changed request field stops the run |
| `sgtp/` | CLI, toy scene, synthetic responder, source lock |
| `catalogues/` | Published catalogues and the compatibility and sanitization reports |
| `examples/`, `tests/`, `docs/`, `README.md` | Examples, offline tests and documentation |

## A missing dependency that was copied in

`embodied_pipeline/data_collection/context_contract.py` imports `.catalog`, and the
worker imports `context_contract` on every run. `embodied_pipeline/data_collection/catalog.py`
is therefore shipped, byte-identical to the workspace file.

## Plan scope adapter

Typed codebooks are hashed with a plan id. The original always drew a random id, so a
saved response could never match a new run's codebook. With `SGTP_PLAN_SCOPE` set (the
CLI sets it from `--plan-scope` or from a recorded file), the plan id is fixed and a
recording can be replayed. `release_backend` refuses to serve a recording under a
different scope instead of repairing it. When the variable is unset, behaviour is the
original one.

The scope only makes codebooks reproducible; it does not identify the request. Each
recorded response therefore also stores the SHA-256 of the exact request body it
answered, and `release_backend` serves it only for a request with the same bytes. A
changed instruction, scene, catalogue or other request field under the same scope is
refused rather than answered with the old plan.

## Publication metadata: `.gitattributes` and `.gitignore`

Copied files are pinned by byte-level SHA-256 in `release_source_lock.json` and
`RELEASE_PROVENANCE.json`, and the catalogue loader checks a hardcoded hash. Git
end-of-line conversion (`core.autocrlf` or a `text` attribute) can rewrite line endings
on checkout, which would make a normal clone fail the lock and the catalogue check.
The root `.gitattributes` contains `* -text`, which unsets the text attribute for every
file so Git stores and checks out the exact bytes on Windows and Linux. Unsetting
`text` does not mark files as binary, so textual diffs still work.

The root `.gitignore` excludes `/runs/` (the README quickstart writes run outputs and
raw backend logs there), `__pycache__/`, compiled Python files, virtual environments
and `.env` files (`.env.example` stays allowed), so `git add --all` does not stage
generated output. Both files are listed in the source lock.
