"""Tests for the meals database (SQLite) behind the get_meal_options tool."""

import json
import sqlite3

import pytest

from mcp_server import db
from mcp_server.tools import get_meal_options


def test_database_has_all_seed_meals():
    seed = json.loads(db.SEED_PATH.read_text(encoding="utf-8"))
    db.ensure_database()
    with sqlite3.connect(db.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM meals").fetchone()[0] == len(seed)


@pytest.mark.parametrize("meal_type", db.MEAL_TYPES)
def test_every_meal_type_has_vegetarian_options(meal_type):
    result = get_meal_options(meal_type, vegetarian=True)
    assert len(result["options"]) >= 2
    assert all(m["vegetarian"] is True for m in result["options"])


def test_macros_match_calories():
    """Calories should be close to 4*protein + 4*carbs + 9*fat (within 10%)."""
    for meal_type in db.MEAL_TYPES:
        for m in get_meal_options(meal_type)["options"]:
            from_macros = 4 * m["protein_g"] + 4 * m["carbs_g"] + 9 * m["fat_g"]
            assert abs(from_macros - m["calories"]) <= 0.10 * m["calories"], m["name"]


def test_options_sorted_by_calories():
    cals = [m["calories"] for m in get_meal_options("lunch")["options"]]
    assert cals == sorted(cals)


def test_bad_seed_row_is_rejected(tmp_path):
    bad = [{"name": "Bad", "meal_type": "brunch", "calories": -5,
            "protein_g": 1, "carbs_g": 1, "fat_g": 1, "vegetarian": True}]
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(bad))
    target = tmp_path / "test.db"
    with pytest.raises(ValueError, match="Bad"):
        db.build_database(db_path=target, seed_path=seed)
    assert not target.exists()  # nothing half-built is left behind


def test_tool_connection_is_read_only():
    db.ensure_database()
    conn = db._read_only()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM meals")
    finally:
        conn.close()


def test_sql_injection_is_harmless():
    assert "error" in get_meal_options("lunch'; DROP TABLE meals; --")
    assert get_meal_options("lunch")["options"]  # table still there


# ---------- Allergies and dislikes ----------
@pytest.mark.parametrize("allergen", db.ALLERGENS)
def test_allergen_filter_removes_every_meal_with_it(allergen):
    for meal_type in db.MEAL_TYPES:
        for m in get_meal_options(meal_type, avoid_allergens=[allergen])["options"]:
            assert allergen not in m["allergens"], m["name"]


def test_common_allergies_still_leave_options_for_every_meal():
    common = ["dairy", "eggs", "gluten", "nuts", "peanuts"]
    for meal_type in db.MEAL_TYPES:
        assert len(get_meal_options(meal_type, avoid_allergens=common)["options"]) >= 2, meal_type


def test_allergen_aliases_and_unknown_allergens():
    result = get_meal_options("snack", avoid_allergens=["Milk", "tree nuts", "peanut"])
    assert result["filtered_out"]["allergens"] == ["dairy", "nuts", "peanuts"]
    assert "error" in get_meal_options("snack", avoid_allergens=["kryptonite"])


def test_dislikes_match_names_and_ingredients_in_singular_or_plural():
    names = [m["name"] for m in get_meal_options("dinner", dislikes=["Chickpeas", "salmon"])["options"]]
    assert names and not any("chana" in n.lower() or "salmon" in n.lower() for n in names)


def test_dislikes_cannot_inject_sql():
    result = get_meal_options("lunch", dislikes=["') OR 1=1 --", "x' ; DROP TABLE meals"])
    assert result["options"]  # odd input is ignored, the table is fine
    assert get_meal_options("lunch")["options"]


def test_bad_allergen_in_seed_is_rejected(tmp_path):
    bad = [{"name": "Moon cake", "meal_type": "snack", "calories": 100, "protein_g": 2, "carbs_g": 20,
            "fat_g": 1, "vegetarian": True, "allergens": ["moon dust"], "ingredients": []}]
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="Moon cake"):
        db.build_database(db_path=tmp_path / "test.db", seed_path=seed)


def test_every_seed_meal_lists_ingredients_and_known_allergens():
    for meal in json.loads(db.SEED_PATH.read_text(encoding="utf-8")):
        assert meal["ingredients"], meal["name"]
        assert set(meal["allergens"]) <= set(db.ALLERGENS), meal["name"]
