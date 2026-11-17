# Planning methods and release scope

## Full-graph three-stage planner (included)

The instruction, context and full supplied scene graph go through task decomposition, object binding and skill matching. The program validates typed structures and assembles an engineering plan or an explicit incomplete artifact. Scene descriptions are input evidence; they do not establish unobserved robot state or execution effects.

## Route-topology preprocessing (included)

A full-scene index supports target prebinding before any crop, so pruning does not deliberately hide duplicate candidates. Ordered targets are mapped to rooms. Deterministic BFS finds each consecutive minimum-hop room path. The selected room context, unknown-room objects and required relation closure are retained before the same three-stage core runs. The prebinding call is additional model work; BFS and JSON cropping are programmatic. Same-room legs use zero room hops; requested visits and revisits remain present. Closed-door routing is not silently treated as physical reachability.

## Room-recursive planning (historical comparison only)

The historical alternative carries a global ordered visit skeleton while loading detailed context incrementally. Each next visit is bound and planned within its room-level segment; door/portal nodes provide symbolic handoff checkpoints. Within-task memory may reuse validated bindings and routes when the scene/version assumptions remain valid. Two successive targets in the same room are handled locally without inventing a door crossing. A return route must not be reversed blindly when direction, obstruction or scene state changes.

The independently packaged executable release does not include the room-recursive runner, TaskAware integration, online scene revision service or robot feedback loop. Its historical scores are retained to compare design alternatives, not to imply all variants are executable from this minimal source tree.

## Shared topology versus supplied context

The historical route-topology and room-recursive branches used the same room/portal foundation but different context selection and planning schedules. The former selects a whole ordered route before core planning; the latter fetches context segment by segment. A topology asset is an input resource, distinct from the final engineering JSON. Routes and scene slices are separate files with provenance, not fields fabricated inside the final plan.

## Execution boundary

Planning-level navigation skills represent desired symbolic transitions. The release excludes object-to-navigation-goal conversion, robot localization, occupancy-map processing, route following, obstacle avoidance, ROS/TCP bridges and hardware commands. Those were external collaborators' work and are not claimed as contributions of this planning repository.
