"""Remembers each user's profile between conversations.

ADK keeps state with a "user:" prefix per user, not per conversation, and the
session service stores it in SQLite (agents/orchestrator/.adk/), so it
survives restarts. After the calorie tool succeeds, the profile it was called
with is saved there; get_meal_options adds whether the user is vegetarian,
their allergies and the foods they dislike.

The instruction in config.yaml reads it back with {user:profile_text?}, so a
returning user doesn't have to type their details again.
"""

import json

PROFILE_KEYS = ("age", "sex", "weight_kg", "height_cm", "activity", "goal")


def _result(tool_response) -> dict:
    """The tool's JSON result from an MCP response ({} if it can't be read)."""
    if not isinstance(tool_response, dict):
        return {}
    for item in tool_response.get("content") or []:
        try:
            return json.loads(item.get("text", ""))
        except (AttributeError, ValueError):
            continue
    structured = tool_response.get("structuredContent")
    if isinstance(structured, dict):
        return structured.get("result", structured)
    return tool_response


def profile_text(profile: dict, vegetarian, allergies=None, dislikes=None) -> str:
    """One readable line for the instruction, e.g. '35-year-old female, 75 kg, ...'."""
    if not profile:
        return ""
    text = (
        f"{profile['age']}-year-old {profile['sex']}, {profile['weight_kg']} kg, "
        f"{profile['height_cm']} cm, activity: {profile['activity']}, goal: {profile['goal']}"
    )
    if vegetarian is not None:
        text += f", vegetarian: {'yes' if vegetarian else 'no'}"
    if allergies is not None:
        text += f", allergies: {', '.join(allergies) or 'none'}"
    if dislikes:
        text += f", dislikes: {', '.join(dislikes)}"
    return text


def remember_profile(tool, args, tool_context, tool_response):
    """after_tool_callback: saves the profile once a tool has used it successfully."""
    state = tool_context.state
    if tool.name == "calculate_daily_calories":
        result = _result(tool_response)
        if "daily_calories" not in result:
            return None  # the tool refused (error or guardrail): nothing to remember
        state["user:profile"] = {key: args.get(key) for key in PROFILE_KEYS}
        state["user:targets"] = result
    elif tool.name == "get_meal_options":
        result = _result(tool_response)
        if "error" in result:
            return None
        if "vegetarian" in args:
            state["user:vegetarian"] = bool(args["vegetarian"])
        filtered = result.get("filtered_out") or {}
        if "avoid_allergens" in args:
            state["user:allergies"] = filtered.get("allergens", [])
        if "dislikes" in args:
            state["user:dislikes"] = filtered.get("dislikes", [])
    else:
        return None
    state["user:profile_text"] = profile_text(
        state.get("user:profile") or {},
        state.get("user:vegetarian"),
        state.get("user:allergies"),
        state.get("user:dislikes"),
    )
    return None  # never changes what the tool returned