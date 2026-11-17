"""Application flow only; no new semantic decisions or LLM stages."""

# Release-only adapter: the workspace package eagerly imported the proposal/feedback
# runners, which depend on the excluded symbolic-proxy executor. This planning-only
# release ships task_collection_worker alone and exports nothing eagerly.
__all__ = []

