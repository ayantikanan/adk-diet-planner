"""Tests for the orchestrator's memory (agents/orchestrator/memory.py)."""

import json

from agents.orchestrator.memory import profile_text, remember_profile

PROFILE = {"age": 35, "sex": "female", "weight_kg": 75, "height_cm": 165, "activity": "light", "goal": "lose"}
TARGETS = {"daily_calories": 1487, "protein_g": 112, "carbs_g": 149, "fat_g": 50}


class Tool:
    def __init__(self, name):
        self.name = name


class Context:
    def __init__(self):
        self.state = {}


def mcp(result: dict) -> dict:
    """What an MCP tool call returns to the agent."""
    return {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False}


def test_profile_saved_after_successful_calculation():
    ctx = Context()
    remember_profile(Tool("calculate_daily_calories"), PROFILE, ctx, mcp(TARGETS))
    assert ctx.state["user:profile"] == PROFILE
    assert ctx.state["user:targets"]["daily_calories"] == 1487
    assert ctx.state["user:profile_text"].startswith("35-year-old female, 75 kg, 165 cm")


def test_vegetarian_added_from_meal_lookup():
    ctx = Context()
    remember_profile(Tool("calculate_daily_calories"), PROFILE, ctx, mcp(TARGETS))
    remember_profile(Tool("get_meal_options"), {"meal_type": "lunch", "vegetarian": True}, ctx, mcp({"options": []}))
    assert ctx.state["user:vegetarian"] is True
    assert ctx.state["user:profile_text"].endswith("vegetarian: yes")


def test_nothing_saved_when_tool_refused():
    ctx = Context()
    remember_profile(Tool("calculate_daily_calories"), {**PROFILE, "age": 14}, ctx, {"error": "BLOCKED by guardrail"})
    remember_profile(Tool("calculate_daily_calories"), PROFILE, ctx, mcp({"error": "sex must be 'male' or 'female'"}))
    assert ctx.state == {}


def test_other_tools_are_ignored_and_results_unchanged():
    ctx = Context()
    assert remember_profile(Tool("grocery_agent"), {"request": "x"}, ctx, {"result": "list"}) is None
    assert ctx.state == {}


def test_profile_text_is_empty_without_a_profile():
    assert profile_text({}, True) == ""


def test_allergies_and_dislikes_are_remembered():
    ctx = Context()
    remember_profile(Tool("calculate_daily_calories"), PROFILE, ctx, mcp(TARGETS))
    args = {"meal_type": "lunch", "vegetarian": False, "avoid_allergens": ["Milk", "nuts"], "dislikes": ["Mushrooms"]}
    tool_result = mcp({"options": [], "filtered_out": {"allergens": ["dairy", "nuts"], "dislikes": ["mushroom"]}})
    remember_profile(Tool("get_meal_options"), args, ctx, tool_result)
    assert ctx.state["user:allergies"] == ["dairy", "nuts"]
    assert ctx.state["user:dislikes"] == ["mushroom"]
    assert "allergies: dairy, nuts, dislikes: mushroom" in ctx.state["user:profile_text"]
