# Command line

Run everything from the release root as `python -m sgtp <command>`.

| Command | Purpose |
|---|---|
| `plan` | Full-graph three-stage planning on a scene directory |
| `topology <subcommand> ...` | The copied topology filter CLI, unchanged |
| `plan-handoff` | Three-stage planning on a finished topology run's cropped scene and task |
| `toy-scene` | Write the synthetic toy scene, overlay and prebinding files |
| `record` | Turn a backend log into a recorded-response file |
| `verify-lock` | Compare the tree with `release_source_lock.json` |

Exit codes: `0` success, `2` usage or input error, `3` controlled stop or incomplete plan.

## Backends (`plan` and `plan-handoff`)

| `--backend` | Behaviour |
|---|---|
| `offline` (default) | No request. The first stage is refused and the run ends incomplete (exit 3) |
| `synthetic` | Exact-label test double `sgtp.synthetic_backend`. Not a model |
| `recorded:<file>` | Serve responses saved by `record`, in order, per stage |
| `scripted:<module>:<callable>` | Call `callable(stage, payload)` for each request (test doubles) |
| `live` | The original transport. Also needs `--acknowledge-live-api`, `--model`, `--base-url` and `--api-key-env` naming a non-empty variable |

A live run may incur provider charges. There is no fallback from another backend to
`live`, and no retry. The key is read from the named environment variable and is never
written to disk by the release code.

### Plan scope and replay

Typed codebooks are hashed together with a plan scope that is normally random. A
recorded response only matches the codebook it was made for. To make a run replayable,
pass `--plan-scope <text>` when recording it:

```
python -m sgtp plan --scene-dir examples/toy_scene --instruction "<INSTRUCTION>" \
    --backend synthetic --plan-scope demo --out-dir runs/plan1
python -m sgtp record --backend-log runs/plan1/backend_log.jsonl --out runs/plan1_recorded.json
python -m sgtp plan --scene-dir examples/toy_scene --instruction "<INSTRUCTION>" \
    --backend recorded:runs/plan1_recorded.json --out-dir runs/plan1_replay
```

The replay takes its scope from the file. A different `--plan-scope` is refused, and
`record` refuses a log made under a random scope.

A scope alone does not make a replay valid. Every recorded response is bound to the
request it answered: `request_sha256`, the SHA-256 of the exact request body bytes.
Before serving a response, the backend hashes the request it is about to send and
serves the next recorded response for that stage only if the hashes are equal. The body
is not parsed or normalised, so the instruction, the scene and its context, the offered
catalogue skills, schemas, codebooks, the model name and the sampling parameters are
all part of the identity. If anything differs (for example the same scope with the
instruction "Visit the blue marker." instead of the recorded one), the stage is refused
(`refused_recorded_replay` in `backend_log.jsonl`), no recorded answer is reused, no
response is served, the run stops with exit code 3, and the engineering plan is
incomplete with `plan: null`. A refused request does not consume the recorded entry.

Other consequences:

- Recording files use format `sgtp_recorded_responses_v2`. Older files (v1, responses
  without request hashes), entries without a valid `request_sha256`, a missing file and
  a log line without a request hash are refused. Record such runs again.
- The recording names its catalogue profile; replaying under another profile is
  refused before any output directory is created.
- A recording of a live run only replays with the same `--model`, because the model
  name is inside the request body.
- Each process keeps its position in a recording file per stage, so one file serves
  one replay per process. Use a copy of the file for a second replay in the same process.

### Other options

`--catalogue-profile` (`formal_v0_3_2_wire_compatible` by default, or
`historical33_replay`), `--request-timeout` (seconds, default 180),
`--semantic-audit-mode` (`annotate` default, `block`, `off`), and for `plan` only:
`--record-id`, `--scene-id`. One process uses one catalogue profile; start a new process
to switch.

## Topology chain on the toy scene

```
python -m sgtp toy-scene --out-dir runs/toy
python -m sgtp topology prepare --run-dir runs/topo --scene-dir runs/toy/scene \
    --overlay runs/toy/overlay.json --record-id sgtp_toy_0001 --start-region-id region_a \
    --instruction "Visit the red marker, then the blue marker, and finally return to the red marker."
python -m sgtp topology ingest-binding --run-dir runs/topo \
    --from-file runs/toy/prebinding_bound.json --source manual_fixture
python -m sgtp topology route --run-dir runs/topo
python -m sgtp topology slice --run-dir runs/topo
python -m sgtp topology handoff --run-dir runs/topo
python -m sgtp plan-handoff --run-dir runs/topo --backend synthetic --out-dir runs/topo_plan
```

On the base toy scene the route legs have hop counts 0, 2 and 2 (total 4). With
`--variant ambiguous` and `prebinding_ambiguous.json`, `ingest-binding` stops with
exit 3 and a question. With `--variant closed_ab`, `route` stops with exit 3 because
room c cannot be reached once portal a-b is closed.

`prepare` needs `--start-region-id` or `--start-object-id`. The start is a declared
scenario assumption, not a measured robot position.
