# Inputs and outputs

## Scene directory

| File | Required | Content |
|---|---|---|
| `scene_graph.json` | yes | Array of nodes |
| `scene_graph_edges.json` | yes | Array of relation hypotheses (`source`, `target`, `relation`) |
| `scene_metadata.json` | recommended | `scene_id`, `coordinate_frame`, `length_unit` |

Each node needs:

- `id`: an integer;
- `object_tag`: the label;
- `caption`: a string;
- `bbox_center` and `bbox_extent`: three finite numbers each, with every extent
  greater than zero;
- `active`: optional. A node with `active: false` is never a candidate.

Edges are unverified relation hypotheses (for example `a on b`). They are never treated
as passages between rooms.

## Topology overlay (topology chain only)

`overlay.json` lists rooms (`regions` with floor rectangles), portals (`connections`
with `door_state`) and an `object_region_index` that assigns each node to one room or
marks it `unknown`. Its provenance must contain the SHA-256 of the scene graph and
edges files it was built for; `prepare` refuses a mismatch. The schema is enforced by
`embodied_pipeline/topology_filter/overlay.py`. `python -m sgtp toy-scene` writes a
complete example.

## Prebinding (topology chain only)

A prebinding document binds every role in the instruction to one object, or stops with
a question. A role is `bound` (one `object_id`) or `ambiguous` (two or more
`candidate_object_ids` and a `clarification_question`). It also carries the visit order
(`visit_role_ids`, revisits kept) and the terminal role. Use `--source manual_fixture`
for a hand-written file and `--source model_response_file` for a saved model output.
This release does not send the prebinding request itself.

## Outputs of `plan` and `plan-handoff`

The `--out-dir` directory must not exist. It contains:

| Path | Content |
|---|---|
| `release_run.json` | Backend, catalogue profile, task, completeness, `model_invoked`, exit status inputs |
| `catalogue_runtime_gap.json` | Formal profile only: offered and withheld catalogue skills |
| `backend_log.jsonl` | One line per backend decision (stage, decision, request and response SHA-256, plan scope) |
| `worker/engineering_plan.json` | The engineering envelope (see below) |
| `worker/skill_plan.json` | The assembled plan, when stage 3 finished |
| `worker/...` | Snapshots, codebooks, raw proposals and audit records from every stage |

Key envelope fields:

- `artifact_completeness`: `complete` or `incomplete`;
- `plan`: the plan, or `null` when incomplete;
- `usable_as_plan`: the automated audits passed. Under the default `annotate` audit
  mode a complete plan can still be `false`; this is a review signal, not a failure;
- `execution_ready`, `robot_executed`, `simulation_executed`: always `false`;
- `failed_stage`, `error_type`, `error`, `partial_evidence`: present when incomplete.

## Outputs of the topology chain

Each stage writes its own directory under `--run-dir` and never overwrites a file:
`prepare/`, `ingest_binding/`, `route/` (`route_plan.json` with per-leg hop counts),
`slice/` (`scene/` holds the cropped scene) and `handoff/` (`task.json`). A stopped stage
writes `engineering_plan.json` with `plan: null` and `stage_failure.json`. A later stage
refuses to run after a stopped or modified predecessor.
