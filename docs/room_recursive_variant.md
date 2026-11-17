# Room-recursive planning (historical, not executable here)

The research workspace also contains a room-recursive planner. This release documents
it for comparison with the historical results only. Its runner, memory and per-visit
request code are not shipped, and no command in this release runs it.

## Idea

- A global, ordered visit skeleton is kept for the whole instruction, revisits included.
- Each next visit is bound and planned within its own room-level segment. Context is
  loaded one segment at a time instead of as one crop for the whole route.
- Door and portal nodes act as symbolic handoff checkpoints between segments.
- Within one task, validated bindings and routes may be reused while the scene version
  and assumptions still hold. Two successive targets in the same room are planned
  locally, without inventing a door crossing. A return route is not reversed blindly.

## Relation to the shipped topology chain

Both variants use the same room and portal overlay. The shipped topology chain selects
the whole ordered route before running the three-stage core once on the crop. The
room-recursive variant fetches context segment by segment and calls the planner per
visit.

## Results

Its saved scores appear in `docs/results.md` next to the full-graph and route-topology
configurations. They are historical diagnostics on a small, same-scene sample. They do
not show that the variant can be run from this source tree.
