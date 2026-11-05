"""Grocery agent: turns dishes into a shopping list.

Served over A2A on its own server: `python serve_agent.py grocery_agent --port 8001` (card: agent.json).
Everything it says and uses comes from config.yaml in this folder.
"""

from pathlib import Path

import yaml
from google.adk.agents import LlmAgent

from .telemetry import export_logs

try:  # running: agents/ is on the import path (serve_agent.py)
    from fallback_gemini import FallbackGemini
except ModuleNotFoundError:  # tests: imported from the repo root as agents.grocery_agent
    from agents.fallback_gemini import FallbackGemini

CONFIG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))

export_logs()

root_agent = LlmAgent(
    name=CONFIG["name"],
    model=FallbackGemini.from_config(CONFIG["models"], CONFIG.get("fallback")),
    description=CONFIG["description"],
    instruction=CONFIG["instruction"],
)
