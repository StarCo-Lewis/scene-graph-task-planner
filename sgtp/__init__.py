"""Scene Graph Task Planner: planning-only release of a three-stage scene-graph planner.

The package is a thin command-line wrapper around the authors' unchanged planning
code under ``embodied_pipeline/`` and ``vendor/legacy_engines/``. Nothing here
executes a plan, contacts a robot or calls a model unless ``--backend live`` and
``--acknowledge-live-api`` are both given.
"""

RELEASE_NAME = "Scene Graph Task Planner"
RELEASE_VERSION = "scene_graph_task_planner_release_20261117"

__all__ = ["RELEASE_NAME", "RELEASE_VERSION"]
