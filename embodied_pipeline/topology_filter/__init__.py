"""Opt-in offline topology input filter.

This package never makes a model request, never touches the network and never
drives a robot. It prepares a prebinding prompt, ingests an externally produced
binding, plans ordered region visits as a minimum-edge-count walk over a
structural region graph, crops the scene graph and hands a cropped-scope task to
the existing collection worker.

Nothing here claims physical verification. The overlay it consumes is a source
asset geometry index: not visual verification, not a robot traversability map.
"""

PIPELINE_VERSION = "topology_input_filter_v1"
CROPPED_SCOPE = "symbolic_cropped_frame_v1"
