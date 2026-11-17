"""Second evidence review of a model's target proposal, before a clarification stops a run.

Opt-in everywhere. With the review switched off, every existing caller produces exactly
the request, document and artifact it produced before this package existed.

What this package is for: a model that proposes ``ambiguous`` may be clarifying something
the supplied source facts already settle. The review re-reads those source facts, per
condition, over the whole graph, and only reports a recovery when one candidate matches
every asserted condition and no competitor is left unexcluded. Suppressing an unnecessary
question is the goal; a higher recovery count is not. A real ambiguity must survive.

Hard rules this package keeps:

* The model's own ``evidence`` prose is never a source fact. Only node labels, node
  captions, the overlay room index and the source edge file are source facts, and every
  verdict names the path it came from.
* Every condition is ``match``, ``contradict`` or ``unknown``. ``unknown`` is neither a
  denial nor positive support. A contradiction with a named source path outranks any score.
* An attribute supports a candidate only where it modifies that candidate's own head noun.
  A white cabinet with a black base is not a colour contradiction for "white cabinet", and
  a cabinet with a white base is not support for it either.
* ``pedestal`` and ``sculpture on a pedestal`` are different heads and are not merged.
* A candidate with no recorded room is never excluded for that.
* The lexical cosine is reporting only by default. It can never override a contradiction,
  never bind on a top score alone and never force a question.
* Nothing here repairs a document that violates a safety contract. An over-long evidence
  field stays over-long and still fails its own length check.
"""

REVIEW_VERSION = "candidate_evidence_review_v1_20261107"
MODE_EVIDENCE_ONLY = "evidence_only"
MODE_EVIDENCE_PLUS_COSINE = "evidence_plus_cosine"
MODE_OFF = "off"
MODES = (MODE_OFF, MODE_EVIDENCE_ONLY, MODE_EVIDENCE_PLUS_COSINE)

__all__ = ["REVIEW_VERSION", "MODE_OFF", "MODE_EVIDENCE_ONLY", "MODE_EVIDENCE_PLUS_COSINE",
           "MODES"]
