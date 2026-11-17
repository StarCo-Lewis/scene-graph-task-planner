# Skill catalogues and profiles

## Files

| File | Content |
|---|---|
| `catalogues/skill_catalogue_v0_3_2.release.json` | Formal symbolic catalogue 0.3.2, 30 skills |
| `catalogues/historical/quadruped_atomic_skill_library_v0_2_0.historical33.json` | Historical library 0.2.0, 33 skills, byte-identical to the original |
| `catalogues/runtime_compatibility_v0_3_2.json` | Which 0.3.2 entries the copied matrix wire can offer, and why the others cannot be offered |
| `catalogues/catalogue_sanitization.json` | Every change made to the 0.3.2 file for publication |

The release copy of 0.3.2 differs from the original only in metadata: internal storage
paths in `source_documents` were replaced by plain file names. The `skills` array is
unchanged; `catalogue_sanitization.json` records the original value hashes and the
canonical hash of the skills array before and after.

## Profiles

`embodied_pipeline/_release.py` selects exactly one catalogue per process and checks its
SHA-256, version and skill list. A mismatch is an error; there is no fallback.

| Profile | Catalogue | Offered skills |
|---|---|---|
| `formal_v0_3_2_wire_compatible` (default) | 0.3.2 | 12 wire-compatible entries |
| `historical33_replay` | 0.2.0 | The original five-skill worker subset |

`historical33_replay` reproduces the historical catalogue and skill subset only. It does
not claim an exact end-to-end replay of any historical run.

## The runtime gap

The copied action-matrix wire has two argument slots. Every non-object parameter must be
an optional number with a finite default, and every skill needs an entry in
`skill_semantic_profiles.json`. Twelve of the 30 entries meet these rules. The other 18
are withheld.

Under the formal profile the execution contract seen by the model gets one extra block,
`release_catalogue_scope`. It lists offered and withheld skills with reasons and states
this rule:

> Only offered_skill_ids are available to this planner. If completing the instruction
> requires a catalogue skill listed in withheld_catalogue_skills, do not substitute an
> offered skill for it; leave that action unplanned so the caller records an incomplete
> result.

The same block is written to `catalogue_runtime_gap.json` in every run. The historical
profile adds no block.

### Unproduced `reachable` preconditions

Catalogue 0.3.0 removed `position_arm_for_object`, the skill that produced `reachable`.
Seven offered skills still require it: `grasp_object`, `place_object_on_surface`,
`place_object_in_container`, `open_articulated_object`, `close_articulated_object`,
`press_control` and `handover_object`. `bend_and_lift_object` requires
`robot.motion_limits_verified`, which no offered skill produces either. The copied plan
checker does not enforce these; it marks the precondition and the step feasibility as
`unknown`. Plans with these skills are symbolic proposals only.

Wire compatibility means the copied code accepts the definition. It is not evidence that
a controller implements the skill or that a plan is executable. The 48 skill
requirements are mapped separately in `docs/skill_requirements.md`.
