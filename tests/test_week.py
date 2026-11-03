"""Tests for the 7-day menu tool (mcp_server/tools/week_tool.py)."""

import pytest

from mcp_server.db import MEAL_TYPES
from mcp_server.tools import build_week_menu, get_meal_options


def test_week_has_seven_full_days_close_to_target():
    week = build_week_menu(1487)
    assert [d["day"] for d in week["days"]][:2] == ["Monday", "Tuesday"] and len(week["days"]) == 7
    for day in week["days"]:
        assert set(day["meals"]) == set(MEAL_TYPES)
        assert day["total_calories"] == sum(m["calories"] for m in day["meals"].values())
        assert abs(day["difference"]) <= 0.10 * 1487


def test_week_has_variety():
    week = build_week_menu(1800)
    assert week["different_dishes"] >= 20
    assert sorted({m["name"] for d in week["days"] for m in d["meals"].values()}) == week["all_dishes"]


def test_portions_scale_to_reach_high_or_low_targets():
    high = build_week_menu(2600)
    assert any(d["portion"] > 1 for d in high["days"])
    low = build_week_menu(1100, vegetarian=True)
    assert all(abs(d["difference"]) <= 0.10 * 1100 for d in low["days"])


@pytest.mark.parametrize("allergens", [["dairy"], ["nuts", "peanuts"], ["gluten", "eggs"]])
def test_week_respects_allergies(allergens):
    week = build_week_menu(1600, avoid_allergens=allergens)
    safe = {m["name"] for t in MEAL_TYPES for m in get_meal_options(t, avoid_allergens=allergens)["options"]}
    unsafe = set(week["all_dishes"]) - safe
    assert not unsafe, unsafe


def test_week_respects_vegetarian_and_dislikes():
    week = build_week_menu(1600, vegetarian=True, dislikes=["chickpeas"])
    assert not any("chickpea" in name.lower() or "chana" in name.lower() for name in week["all_dishes"])
    assert week["filtered_out"]["dislikes"] == ["chickpea"]


def test_week_rejects_bad_input():
    assert "error" in build_week_menu(300)
    assert "error" in build_week_menu(1500, avoid_allergens=["kryptonite"])


def test_same_input_gives_same_week():
    assert build_week_menu(1700, vegetarian=True) == build_week_menu(1700, vegetarian=True)
