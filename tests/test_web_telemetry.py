"""Tests for the web app's telemetry (ui/web_telemetry.py): the user-facing SLI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui"))
import web_telemetry as wt  # noqa: E402


def test_request_kinds():
    assert wt.request_kind("I'm 35, female, 75 kg, 165 cm, lightly active. Plan my whole week (7 days).") == "week"
    assert wt.request_kind("I'm 35, female, 75 kg, 165 cm, lightly active, want to lose weight.") == "day"
    assert wt.request_kind("Swap my lunch (Rajma) for a different option with similar calories.") == "swap"
    assert wt.request_kind("Can the snack be lighter?") == "followup"


def test_plan_timer_records_one_outcome(monkeypatch):
    counted, timed = [], []
    monkeypatch.setattr(wt.plan_requests, "add", lambda value, attrs: counted.append((value, attrs)))
    monkeypatch.setattr(wt.plan_duration, "record", lambda value, attrs: timed.append((value, attrs)))
    timer = wt.PlanTimer("Swap my dinner (Tofu stir-fry) for a different option with similar calories.")
    timer.finish("error", "Lost connection to the orchestrator")
    timer.finish("success")  # ignored: the first outcome counts
    assert counted == [(1, {"kind": "swap", "outcome": "error"})]
    assert len(timed) == 1 and timed[0][0] >= 0


def test_setup_does_nothing_without_an_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert wt.setup(app=None) is False
