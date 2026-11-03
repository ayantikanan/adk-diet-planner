"""Checks that every agent, tool and setting really comes from its config.yaml."""

import sys
from pathlib import Path

import yaml
from google.adk.tools import FunctionTool
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent


def load(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


ORCHESTRATOR = load("agents/orchestrator/config.yaml")
GROCERY = load("agents/grocery_agent/config.yaml")
WORKOUT = load("agents/workout_agent/config.yaml")
MCP = load("mcp_server/config.yaml")


def test_orchestrator_uses_its_config():
    from agents.orchestrator.agent import grocery_agent, root_agent, workout_agent

    assert root_agent.name == ORCHESTRATOR["name"]
    assert root_agent.instruction == ORCHESTRATOR["instruction"]
    assert root_agent.description == ORCHESTRATOR["description"]
    assert root_agent.model.model_names == ORCHESTRATOR["models"]
    assert grocery_agent.description == ORCHESTRATOR["sub_agents"]["grocery_agent"]["description"]
    assert workout_agent.description == ORCHESTRATOR["sub_agents"]["workout_agent"]["description"]


def test_sub_agents_use_their_config():
    from agents.grocery_agent.agent import root_agent as grocery
    from agents.workout_agent.agent import root_agent as workout

    for agent, cfg in [(grocery, GROCERY), (workout, WORKOUT)]:
        assert agent.name == cfg["name"]
        assert agent.instruction == cfg["instruction"]
        assert agent.model.model == cfg["model"]


def test_workout_tool_description_and_numbers_from_config():
    from agents.workout_agent.tools import EXERCISE_MET, estimate_calories_burned

    tool_cfg = WORKOUT["tools"]["estimate_calories_burned"]
    declaration = FunctionTool(estimate_calories_burned)._get_declaration()
    assert declaration.description.startswith("Estimates calories burned for one exercise.")
    assert EXERCISE_MET == tool_cfg["exercise_met"]


def test_calorie_numbers_from_config():
    from mcp_server import config

    tool_cfg = MCP["tools"]["calculate_daily_calories"]
    assert config.ACTIVITY_LEVELS == tool_cfg["activity_levels"]
    assert config.MIN_CALORIES == tool_cfg["min_calories"]


def test_orchestrator_points_at_mcp_server():
    assert ORCHESTRATOR["mcp_server"]["url"] == f"http://{MCP['host']}:{MCP['port']}/mcp"


async def test_mcp_server_serves_config_descriptions():
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server" / "server.py"), "--stdio"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = {t.name: t.description for t in (await session.list_tools()).tools}

    assert init.server_info.name == MCP["name"]
    for name in ("calculate_daily_calories", "get_meal_options", "build_week_menu"):
        assert tools[name] == MCP["tools"][name]["description"]
