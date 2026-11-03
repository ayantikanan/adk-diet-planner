"""Guardrails for the orchestrator. The rules live in guardrails.yaml.

Three checkpoints, plugged into the agent as ADK callbacks (see agent.py):
  1. check_input   (before_model_callback): a new user message that matches a
     rule gets that rule's reply, and Gemini is never called.
  2. check_tool    (before_tool_callback): tool arguments outside the safe
     range are refused before the tool runs.
  3. check_output  (after_model_callback): the final answer gets the medical
     disclaimer if it is missing, and anything that looks like a secret is
     removed.

Every time a guardrail fires it is recorded three ways, so it shows up in the
observability stack: a metric (diet_guardrail_triggers_total, by rule), a span
attribute (guardrail.rule) and a warning log line.
"""

import logging
import re
from pathlib import Path

import yaml
from google.adk.models import LlmResponse
from google.genai import types
from opentelemetry import metrics, trace

RULES = yaml.safe_load((Path(__file__).parent / "guardrails.yaml").read_text(encoding="utf-8"))

INPUT_RULES = [
    (rule["name"], [re.compile(p, re.IGNORECASE) for p in rule["patterns"]], rule["reply"].strip())
    for rule in RULES["input_rules"]
]
TOOL_LIMITS = RULES["tool_limits"]
DISCLAIMER = RULES["output"]["disclaimer"]
REDACT = [re.compile(p) for p in RULES["output"]["redact_patterns"]]

log = logging.getLogger("google_adk.guardrails")  # exported to Loki with ADK's logs
triggers = metrics.get_meter("orchestrator").create_counter(
    "diet.guardrail_triggers", description="Times a guardrail blocked or changed something, by rule"
)


def _record(rule: str, detail: str) -> None:
    triggers.add(1, {"rule": rule})
    trace.get_current_span().set_attribute("guardrail.rule", rule)
    log.warning("guardrail %s fired: %s", rule, detail)


# ---------- Pure checks (easy to test) ----------
def match_input(text: str) -> tuple[str, str] | None:
    """Returns (rule name, reply) for the first rule the text matches, else None."""
    for name, patterns, reply in INPUT_RULES:
        if any(p.search(text) for p in patterns):
            return name, reply
    return None


def check_tool_args(tool_name: str, args: dict) -> str | None:
    """Returns a refusal message if an argument is outside its safe range, else None."""
    for arg, (low, high) in TOOL_LIMITS.get(tool_name, {}).items():
        value = args.get(arg)
        try:
            out_of_range = value is not None and not (low <= float(value) <= high)
        except (TypeError, ValueError):
            out_of_range = True
        if out_of_range:
            return RULES["tool_refusal"].format(arg=arg, value=value, low=low, high=high)
    return None


def clean_output(text: str, add_disclaimer: bool = True) -> tuple[str, list[str]]:
    """Removes secrets and adds the disclaimer to a plan. Returns (text, what changed)."""
    changes = []
    for pattern in REDACT:
        text, n = pattern.subn("[removed]", text)
        if n:
            changes.append("redacted_secret")
    is_plan = "|" in text or re.search(r"\bk?cal(orie)?s?\b", text, re.IGNORECASE)
    if add_disclaimer and is_plan and DISCLAIMER.lower() not in text.lower():
        text = f"{text.rstrip()}\n\n{DISCLAIMER}"
        changes.append("added_disclaimer")
    return text, changes


# ---------- ADK callbacks ----------
def _latest_user_text(llm_request) -> str | None:
    """The user's newest message, or None when the model is mid-way through tool calls."""
    if not llm_request.contents:
        return None
    last = llm_request.contents[-1]
    if last.role != "user":
        return None
    texts = [p.text for p in (last.parts or []) if p.text]
    return " ".join(texts) if texts else None


def check_input(callback_context, llm_request) -> LlmResponse | None:
    text = _latest_user_text(llm_request)
    if not text:
        return None
    hit = match_input(text)
    if not hit:
        return None
    name, reply = hit
    _record(name, "input blocked before the model")
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=reply)]))


def check_tool(tool, args, tool_context) -> dict | None:
    refusal = check_tool_args(tool.name, args)
    if refusal:
        _record(f"tool_limit:{tool.name}", refusal.splitlines()[0])
        return {"error": refusal}
    return None


def check_output(callback_context, llm_response) -> LlmResponse | None:
    parts = (llm_response.content and llm_response.content.parts) or []
    if not parts or any(p.function_call for p in parts):
        return None  # not the final answer yet
    text_parts = [p for p in parts if p.text and not p.thought]
    changed = False
    for i, part in enumerate(text_parts):
        part.text, changes = clean_output(part.text, add_disclaimer=(i == len(text_parts) - 1))
        for change in changes:
            _record(change, "final answer adjusted")
        changed = changed or bool(changes)
    return llm_response if changed else None
