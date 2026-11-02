"""Checks the grocery agent is set up correctly for A2A (no API key needed)."""

import json
from pathlib import Path

from agents.grocery_agent.agent import root_agent

CARD = Path(__file__).resolve().parent.parent / "agents" / "grocery_agent" / "agent.json"


def test_agent_card_matches_agent():
    card = json.loads(CARD.read_text(encoding="utf-8"))
    assert card["name"] == root_agent.name == "grocery_agent"
    assert card["url"] == "http://localhost:8001/a2a/grocery_agent"
    assert card["skills"][0]["id"] == "shopping_list"


def test_orchestrator_points_to_card():
    from agents.orchestrator.agent import CONFIG, grocery_agent

    assert grocery_agent.name == "grocery_agent"
    card = json.loads(CARD.read_text(encoding="utf-8"))
    assert CONFIG["sub_agents"]["grocery_agent"]["url"] == card["url"]
