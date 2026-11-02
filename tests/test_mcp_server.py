"""Tests the MCP server over real stdio, exactly how the agent talks to it."""

import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER_FILE = Path(__file__).resolve().parent.parent / "mcp_server" / "server.py"


def _data(result):
    return json.loads(result.content[0].text)


async def _call(tool, args):
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER_FILE), "--stdio"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            return names, await session.call_tool(tool, args)


async def test_server_lists_both_tools():
    names, _ = await _call("get_meal_options", {"meal_type": "lunch"})
    assert names == {"calculate_daily_calories", "get_meal_options"}


async def test_calorie_tool_over_mcp():
    _, result = await _call(
        "calculate_daily_calories",
        {"age": 35, "sex": "female", "weight_kg": 75, "height_cm": 165,
         "activity": "light", "goal": "lose"},
    )
    assert _data(result)["daily_calories"] == 1487


async def test_meal_tool_over_mcp():
    _, result = await _call("get_meal_options", {"meal_type": "dinner", "vegetarian": True})
    assert all(m["vegetarian"] for m in _data(result)["options"])
