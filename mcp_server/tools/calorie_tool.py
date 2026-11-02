"""Tool 1: calculates a daily calorie and macro target."""

from opentelemetry import metrics

from mcp_server.config import ACTIVITY_LEVELS, GOAL_ADJUSTMENT, MACRO_SPLIT, MIN_CALORIES

# Business metric: how many calorie targets were calculated, by goal and sex.
calorie_targets = metrics.get_meter("diet_mcp_server").create_counter(
    "diet.calorie_targets", description="Calorie targets calculated, by goal"
)


def calculate_daily_calories(
    age: int, sex: str, weight_kg: float, height_cm: float, activity: str, goal: str
) -> dict:
    """Description for the AI lives in mcp_server/config.yaml (tools.calculate_daily_calories.description)."""
    sex, activity, goal = sex.lower(), activity.lower(), goal.lower()

    if sex not in MIN_CALORIES:
        return {"error": "sex must be 'male' or 'female'"}
    if activity not in ACTIVITY_LEVELS:
        return {"error": f"activity must be one of: {', '.join(ACTIVITY_LEVELS)}"}
    if goal not in GOAL_ADJUSTMENT:
        return {"error": f"goal must be one of: {', '.join(GOAL_ADJUSTMENT)}"}

    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if sex == "male" else -161)
    calories = bmr * ACTIVITY_LEVELS[activity] + GOAL_ADJUSTMENT[goal]
    calories = max(round(calories), MIN_CALORIES[sex])
    calorie_targets.add(1, {"goal": goal, "sex": sex})

    return {
        "daily_calories": calories,
        "protein_g": round(calories * MACRO_SPLIT["protein"] / 4),
        "carbs_g": round(calories * MACRO_SPLIT["carbs"] / 4),
        "fat_g": round(calories * MACRO_SPLIT["fat"] / 9),
    }
