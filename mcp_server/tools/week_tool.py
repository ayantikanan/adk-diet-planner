"""Tool 3: builds a 7-day menu from the meals database.

Each day gets one breakfast, lunch, dinner and snack, repeating dishes as
little as possible across the week. If a day's dishes add up to too little or
too much, the portion size is adjusted (0.75x to 1.5x, in quarter steps) to get
close to the daily calorie target. The choice is a small search over every
combination, so the numbers are exact and the same input gives the same week.
"""

from itertools import product

from mcp_server.db import MEAL_TYPES, fetch_meals
from mcp_server.tools.meal_tool import _allergens, _dislikes, allergen_filters

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
REPEAT_PENALTY = 0.08  # one repeat "costs" the same as missing the target by 8%
PORTIONS = (0.75, 1.0, 1.25, 1.5)


def _portion(total: int, target: int) -> float:
    """The portion size (from PORTIONS) that brings the total closest to the target."""
    return min(PORTIONS, key=lambda p: (abs(total * p - target), abs(p - 1)))


def build_week_menu(
    daily_calories: int,
    vegetarian: bool = False,
    avoid_allergens: list[str] | None = None,
    dislikes: list[str] | None = None,
) -> dict:
    """Description for the AI lives in mcp_server/config.yaml (tools.build_week_menu.description)."""
    if not 1000 <= int(daily_calories) <= 5000:
        return {"error": "daily_calories must be between 1000 and 5000 (use calculate_daily_calories first)"}
    allergens, unknown = _allergens(avoid_allergens or [])
    if unknown:
        return {"error": f"unknown allergen(s): {', '.join(unknown)}"}
    words = _dislikes(dislikes or [])
    for allergen in allergens:
        allergen_filters.add(1, {"allergen": allergen})

    options = {m: fetch_meals(m, vegetarian, allergens, words, limit=50) for m in MEAL_TYPES}
    missing = [m for m, meals in options.items() if not meals]
    if missing:
        return {"error": f"no safe {', '.join(missing)} options with these filters", "filtered_out": {"allergens": allergens, "dislikes": words}}

    target = int(daily_calories)
    uses: dict[str, int] = {}
    days = []
    for day in DAYS:
        def score(combo):
            base = sum(d["calories"] for d in combo)
            portion = _portion(base, target)
            return (
                abs(base * portion - target) / target
                + 0.02 * abs(portion - 1)  # prefer normal portions when it's a tie
                + REPEAT_PENALTY * sum(uses.get(d["name"], 0) for d in combo)
            )

        best = min(product(*(options[m] for m in MEAL_TYPES)), key=score)
        for dish in best:
            uses[dish["name"]] = uses.get(dish["name"], 0) + 1
        portion = _portion(sum(d["calories"] for d in best), target)
        meals = {
            m: {"name": d["name"], **{k: round(d[k] * portion) for k in ("calories", "protein_g", "carbs_g", "fat_g")}}
            for m, d in zip(MEAL_TYPES, best)
        }
        total = sum(meal["calories"] for meal in meals.values())
        days.append({"day": day, "portion": portion, "meals": meals, "total_calories": total, "difference": total - target})

    result = {
        "daily_target": target,
        "days": days,
        "different_dishes": len(uses),
        "all_dishes": sorted(uses),
    }
    if allergens or words:
        result["filtered_out"] = {"allergens": allergens, "dislikes": words}
    far = [d["day"] for d in days if abs(d["difference"]) > 0.10 * target]
    if far:
        result["note"] = (
            f"On {', '.join(far)} the menu is more than 10% away from the target even with adjusted "
            "portions. Suggest an extra snack."
        )
    return result
