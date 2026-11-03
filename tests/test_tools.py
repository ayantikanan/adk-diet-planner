"""Tests the tools without calling Gemini (no API key needed)."""

from mcp_server.tools import calculate_daily_calories, get_meal_options


def test_calories_for_weight_loss():
    result = calculate_daily_calories(30, "female", 65, 165, "light", "lose")
    assert result["daily_calories"] == 1384
    assert result["protein_g"] == 104


def test_calories_never_below_safety_floor():
    result = calculate_daily_calories(60, "female", 45, 150, "sedentary", "lose")
    assert result["daily_calories"] == 1200


def test_bad_activity_returns_error():
    assert "error" in calculate_daily_calories(30, "male", 80, 180, "extreme", "gain")


def test_vegetarian_filter():
    result = get_meal_options("dinner", vegetarian=True)
    assert all(m["vegetarian"] for m in result["options"])
    assert len(result["options"]) >= 3


def test_unknown_meal_type_returns_error():
    assert "error" in get_meal_options("brunch")
