"""Tests the MCP server over Streamable HTTP, the transport the orchestrator uses."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

SERVER_FILE = Path(__file__).resolve().parent.parent / "mcp_server" / "server.py"
TEST_PORT = "8012"  # not 8002, so it never clashes with a server you have running
URL = f"http://127.0.0.1:{TEST_PORT}/mcp"


@pytest.fixture
def http_server():
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_FILE)],
        env={**os.environ, "MCP_PORT": TEST_PORT},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):  # wait up to ~10 s for the port to open
        try:
            httpx.get(f"http://127.0.0.1:{TEST_PORT}/", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    yield
    proc.terminate()
    proc.wait(timeout=10)


async def test_tools_over_streamable_http(http_server):
    async with streamable_http_client(URL) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            result = await session.call_tool(
                "calculate_daily_calories",
                {"age": 35, "sex": "female", "weight_kg": 75, "height_cm": 165,
                 "activity": "light", "goal": "lose"},
            )

    assert names == {"calculate_daily_calories", "get_meal_options", "build_week_menu"}
    assert json.loads(result.content[0].text)["daily_calories"] == 1487
