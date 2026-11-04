"""Checks the workout agent and its tool (no API key needed)."""

import json
from pathlib import Path

from agents.workout_agent.agent import root_agent
from agents.workout_agent.tools import estimate_calories_burned

CARD = Path(__file__).resolve().parent.parent / "agents" / "workout_agent" / "agent.json"


def test_agent_card_matches_agent():
    card = json.loads(CARD.read_text(encoding="utf-8"))
    assert card["name"] == root_agent.name == "workout_agent"
    assert card["url"] == "http://localhost:8003/a2a/workout_agent"


def test_orchestrator_points_to_card():
    from agents.orchestrator.agent import CONFIG

    card = json.loads(CARD.read_text(encoding="utf-8"))
    assert CONFIG["sub_agents"]["workout_agent"]["url"] == card["url"]


def test_calories_burned_jogging():
    # 7.0 MET x 3.5 x 75 kg / 200 x 30 min = 275.6 -> 276
    assert estimate_calories_burned("jogging", 30, 75)["calories_burned"] == 276


def test_unknown_exercise_returns_error():
    assert "error" in estimate_calories_burned("parkour", 30, 75)
