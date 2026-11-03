"""Checks for the observability stack config (observability/stack/)."""

import json
from pathlib import Path

import yaml

STACK = Path(__file__).resolve().parents[1] / "observability" / "stack"


def load(name):
    return yaml.safe_load((STACK / name).read_text(encoding="utf-8"))


def test_gcp_addon_keeps_every_local_exporter():
    """The Google Cloud add-on replaces the pipeline exporter lists, so it must repeat the local ones."""
    base = load("otel-collector.yaml")["service"]["pipelines"]
    gcp = load("otel-collector.gcp.yaml")["service"]["pipelines"]
    for signal, pipeline in gcp.items():
        assert set(base[signal]["exporters"]) <= set(pipeline["exporters"]), signal
        assert "googlecloud" in pipeline["exporters"]


def test_dashboard_rows_and_unique_panel_ids():
    dashboard = json.loads((STACK / "grafana" / "dashboards" / "diet-planner.json").read_text(encoding="utf-8"))
    rows = [p["title"] for p in dashboard["panels"] if p["type"] == "row"]
    assert rows[:3] == ["Overview", "SLOs & alerts (measured at the web app)", "Safety & business"]
    ids = [p["id"] for p in dashboard["panels"]]
    assert len(ids) == len(set(ids))
    assert {v["name"] for v in dashboard["templating"]["list"]} >= {"price_in", "price_out"}
