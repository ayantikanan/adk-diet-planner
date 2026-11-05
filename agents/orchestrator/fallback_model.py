"""The orchestrator's model: Gemini with fallbacks (shared with the sub-agents, see agents/fallback_gemini.py)."""

try:  # running: agents/ is on the import path (adk web / serve_agent.py)
    from fallback_gemini import FallbackGemini
except ModuleNotFoundError:  # tests: imported from the repo root as agents.orchestrator
    from agents.fallback_gemini import FallbackGemini

__all__ = ["FallbackGemini"]
