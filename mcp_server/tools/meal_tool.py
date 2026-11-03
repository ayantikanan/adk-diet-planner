"""Tool 2: returns meal ideas from the meals database (SQLite).

Filters: vegetarian, allergens to avoid, and foods the user dislikes.
"""

import re

from opentelemetry import metrics

from mcp_server.db import ALLERGENS, MEAL_TYPES, fetch_meals

# Other words people (or the model) use for the same allergen.
ALLERGEN_ALIASES = {
    "milk": "dairy", "lactose": "dairy", "cheese": "dairy",
    "egg": "eggs",
    "seafood": "fish",
    "shrimp": "shellfish", "prawn": "shellfish", "prawns": "shellfish", "crustaceans": "shellfish",
    "wheat": "gluten", "coeliac": "gluten", "celiac": "gluten",
    "nut": "nuts", "tree nuts": "nuts", "tree nut": "nuts",
    "peanut": "peanuts",
    "soya": "soy", "soybean": "soy",
    "sesame seeds": "sesame",
}
_WORD = re.compile(r"^[a-z][a-z \-]{1,29}$")

# Business metric: which allergens people avoid.
allergen_filters = metrics.get_meter("diet_mcp_server").create_counter(
    "diet.allergen_filters", description="Meal lookups that filtered out an allergen, by allergen"
)


def _allergens(values: list[str]) -> tuple[list[str], list[str]]:
    """Maps names to known allergens. Returns (known, unknown)."""
    known, unknown = [], []
    for value in values:
        name = ALLERGEN_ALIASES.get(value.strip().lower(), value.strip().lower())
        (known if name in ALLERGENS else unknown).append(name)
    return sorted(set(known)), unknown


def _dislikes(values: list[str]) -> list[str]:
    """Cleans up dislikes: lower case, singular ('mushrooms' -> 'mushroom'), letters only."""
    words = []
    for value in values[:10]:
        word = value.strip().lower()
        if not _WORD.match(word):
            continue
        if word.endswith("es") and len(word) > 4:
            word = word[:-2]
        elif word.endswith("s") and len(word) > 3:
            word = word[:-1]
        words.append(word)
    return sorted(set(words))


def get_meal_options(
    meal_type: str,
    vegetarian: bool = False,
    avoid_allergens: list[str] | None = None,
    dislikes: list[str] | None = None,
) -> dict:
    """Description for the AI lives in mcp_server/config.yaml (tools.get_meal_options.description)."""
    meal_type = meal_type.lower().strip()
    if meal_type not in MEAL_TYPES:
        return {"error": f"meal_type must be one of: {', '.join(MEAL_TYPES)}"}
    allergens, unknown = _allergens(avoid_allergens or [])
    if unknown:
        return {"error": f"unknown allergen(s): {', '.join(unknown)}. Use any of: {', '.join(ALLERGENS)}"}
    words = _dislikes(dislikes or [])

    for allergen in allergens:
        allergen_filters.add(1, {"allergen": allergen})
    result = {"meal_type": meal_type, "options": fetch_meals(meal_type, vegetarian, allergens, words)}
    if allergens or words:
        result["filtered_out"] = {"allergens": allergens, "dislikes": words}
    if not result["options"]:
        result["note"] = "No meal of this type is safe with these filters. Tell the user; don't suggest one."
    return result
