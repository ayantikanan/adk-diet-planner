"""MCP server settings, read from mcp_server/config.yaml."""

from pathlib import Path

import yaml

SERVER_DIR = Path(__file__).resolve().parent
SERVER = yaml.safe_load((SERVER_DIR / "config.yaml").read_text(encoding="utf-8"))
TOOLS = SERVER["tools"]

# Numbers used by the calorie tool
_calories = TOOLS["calculate_daily_calories"]
ACTIVITY_LEVELS = _calories["activity_levels"]
GOAL_ADJUSTMENT = _calories["goal_adjustment"]
MIN_CALORIES = _calories["min_calories"]
MACRO_SPLIT = _calories["macro_split"]
