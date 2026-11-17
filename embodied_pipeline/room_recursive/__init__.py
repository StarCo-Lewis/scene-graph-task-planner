"""Room-granularity recursive symbolic planning, v1.

Ordered navigation visits only, three controlled sequences on scene200. Doors are
symbolic handover checkpoints: the overlay records structural portal candidates
whose traversability is unknown, so nothing here is a navigation claim.
"""

EXPERIMENT_ID = "room_recursive_symbolic_v1"
PLAN_VERSION = "room_recursive_plan_v1"
SLICE_VERSION = "room_recursive_slice_v1"
REPLAY_VERSION = "room_recursive_replay_v1"
RECURSIVE_ARM_ID = "room_recursive_symbolic_v1"
BASELINE_ARM_ID = "topology_cropped_whole_route"
#: The baseline arm is produced by the existing topology filter CLI, unchanged. No
#: code in this package participates in it.
BASELINE_IS_EXISTING_CLI = True
