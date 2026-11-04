"""Workout agent: suggests a daily workout and the calories it burns.

Served over A2A on its own server: `python serve_agent.py workout_agent --port 8003` (card: agent.json).
Everything it says and uses comes from config.yaml in this folder.
"""

from pathlib import Path

import yaml
from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.genai import types

from .telemetry import export_logs
from .tools import estimate_calories_burned

CONFIG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))
RETRY = CONFIG["retry"]

export_logs()

root_agent = LlmAgent(
    name=CONFIG["name"],
    model=Gemini(
        model=CONFIG["model"],
        retry_options=types.HttpRetryOptions(
            attempts=RETRY["attempts"],
            initial_delay=RETRY["initial_delay_seconds"],
            http_status_codes=RETRY["http_status_codes"],
        ),
    ),
    description=CONFIG["description"],
    instruction=CONFIG["instruction"],
    tools=[estimate_calories_burned],
)
