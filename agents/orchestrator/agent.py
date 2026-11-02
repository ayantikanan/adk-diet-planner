"""Orchestrator agent (the root agent the user talks to).

- Tools: every tool on the MCP server (mcp_server/), over Streamable HTTP.
- Sub-agents: grocery_agent and workout_agent, over A2A, called like tools.
Everything it says and connects to comes from config.yaml in this folder.
On Google Cloud, the addresses are overridden with environment variables:
MCP_SERVER_URL, GROCERY_AGENT_URL and WORKOUT_AGENT_URL.
"""

import os
from pathlib import Path

import yaml
from google.adk.agents import LlmAgent
from google.adk.agents.remote_a2a_agent import AGENT_CARD_WELL_KNOWN_PATH, RemoteA2aAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

from .fallback_model import FallbackGemini
from .telemetry import export_logs

CONFIG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))

export_logs()

# MCP: connect to the tool server and load all its tools.
mcp_tools = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=os.getenv("MCP_SERVER_URL", CONFIG["mcp_server"]["url"]),
        timeout=CONFIG["mcp_server"]["timeout_seconds"],
    ),
)


# A2A: each sub-agent is found through its agent card.
def remote_agent(name: str) -> RemoteA2aAgent:
    sub = CONFIG["sub_agents"][name]
    url = os.getenv(f"{name.upper()}_URL", sub["url"])  # e.g. GROCERY_AGENT_URL
    return RemoteA2aAgent(
        name=name,
        description=sub["description"],
        agent_card=f"{url.rstrip('/')}{AGENT_CARD_WELL_KNOWN_PATH}",
    )


grocery_agent = remote_agent("grocery_agent")
workout_agent = remote_agent("workout_agent")

root_agent = LlmAgent(
    name=CONFIG["name"],
    model=FallbackGemini(model_names=CONFIG["models"]),
    description=CONFIG["description"],
    instruction=CONFIG["instruction"],
    tools=[
        mcp_tools,                 # MCP tools (Streamable HTTP)
        AgentTool(grocery_agent),  # A2A sub-agent 1
        AgentTool(workout_agent),  # A2A sub-agent 2
    ],
)
