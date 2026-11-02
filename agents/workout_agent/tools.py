"""The workout agent's own tool. Its description and MET values come from config.yaml."""

from pathlib import Path

import yaml
from opentelemetry import metrics

CONFIG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))
TOOL = CONFIG["tools"]["estimate_calories_burned"]

# How hard each exercise is compared to resting (1.0).
EXERCISE_MET = TOOL["exercise_met"]

# Business metric: which exercises the agent suggests.
exercises_suggested = metrics.get_meter("workout_agent").create_counter(
    "diet.workout_exercises", description="Exercises suggested by the workout agent"
)


def estimate_calories_burned(exercise: str, minutes: int, weight_kg: float) -> dict:
    met = EXERCISE_MET.get(exercise.lower())
    if met is None:
        return {"error": f"exercise must be one of: {', '.join(EXERCISE_MET)}"}
    # Standard formula: calories = MET x 3.5 x weight(kg) / 200 x minutes
    calories = round(met * 3.5 * weight_kg / 200 * minutes)
    exercises_suggested.add(1, {"exercise": exercise.lower()})
    return {"exercise": exercise.lower(), "minutes": minutes, "calories_burned": calories}


# ADK reads a tool's description from its docstring: take it from config.yaml.
estimate_calories_burned.__doc__ = TOOL["description"]
