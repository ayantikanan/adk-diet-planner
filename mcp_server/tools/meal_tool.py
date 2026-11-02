"""Tool 2: returns meal ideas from the meals database (SQLite)."""

from mcp_server.db import MEAL_TYPES, fetch_meals


def get_meal_options(meal_type: str, vegetarian: bool = False) -> dict:
    """Description for the AI lives in config/agents.yaml (tools.get_meal_options.description)."""
    meal_type = meal_type.lower().strip()
    if meal_type not in MEAL_TYPES:
        return {"error": f"meal_type must be one of: {', '.join(MEAL_TYPES)}"}

    return {"meal_type": meal_type, "options": fetch_meals(meal_type, vegetarian)}
