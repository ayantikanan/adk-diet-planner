"""Each A2A sub-agent runs on its own server (serve_agent.py); no API key needed."""

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
AGENTS = {"grocery_agent": 8001, "workout_agent": 8003}


@pytest.mark.parametrize("name,port", AGENTS.items())
def test_agent_serves_its_card_on_its_own_server(name, port):
    from serve_agent import build_app

    with TestClient(build_app(name, port=port)) as client:
        response = client.get(f"/a2a/{name}/.well-known/agent-card.json")
    assert response.status_code == 200
    assert response.json()["name"] == name
    assert response.json()["url"] == f"http://localhost:{port}/a2a/{name}"


def test_agents_are_on_different_ports():
    from agents.orchestrator.agent import CONFIG

    urls = {name: CONFIG["sub_agents"][name]["url"] for name in AGENTS}
    assert len({url.split("/a2a/")[0] for url in urls.values()}) == len(AGENTS)
    ui_checks = (ROOT / "ui" / "config.yaml").read_text(encoding="utf-8")
    for name, url in urls.items():
        card = json.loads((ROOT / "agents" / name / "agent.json").read_text(encoding="utf-8"))
        assert card["url"] == url
        assert f"{url}/.well-known/agent-card.json" in ui_checks


def test_container_starts_each_agent_on_its_own_port():
    from deploy.start import AGENT_PORTS

    assert {name: int(port) for name, port in AGENT_PORTS.items()} == AGENTS
