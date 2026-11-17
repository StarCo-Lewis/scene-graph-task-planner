# Examples

`toy_scene/` is a small synthetic scene owned by this release. It contains no real
scene data and no robot data.

| File | Content |
|---|---|
| `toy_scene/scene_graph.json` | 11 nodes: four room floors, a red marker (id 10), a blue marker (id 11), a wall panel straddling rooms a and b, and four furniture items |
| `toy_scene/scene_graph_edges.json` | Two unverified `a on b` support hypotheses (never passages) |
| `toy_scene/scene_metadata.json` | `scene_id` `sgtp_toy_scene`, frame `synthetic_world_z_up_m`, metres |

These three files are identical (as parsed JSON) to the output of `sgtp/toy.py`
for the `base` variant; `tests/test_release_offline.py` checks this.

The topology overlay and the hand-authored prebinding files are not stored here.
Generate them, together with a fresh copy of the scene, with:

```
python -m sgtp toy-scene --out-dir runs/toy                       # base
python -m sgtp toy-scene --out-dir runs/toy_amb --variant ambiguous
python -m sgtp toy-scene --out-dir runs/toy_closed --variant closed_ab
```

Variants:

- `base`: every target label is unique.
- `ambiguous`: adds a second `blue marker` (id 13) in room d, plus a
  `prebinding_ambiguous.json` that stops with a clarification question.
- `closed_ab`: portal a-b is declared closed, so room c cannot be reached from room a.

The toy instruction is:

```
Visit the red marker, then the blue marker, and finally return to the red marker.
```

See `docs/cli.md` for the full command sequence.
