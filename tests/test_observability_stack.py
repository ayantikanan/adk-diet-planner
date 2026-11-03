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


def test_alerts_go_to_alertmanager_and_slack_secret_stays_out_of_git():
    assert load("prometheus.yml")["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == ["alertmanager:9093"]
    slack = load("alertmanager/alertmanager.yml")["receivers"][0]["slack_configs"][0]
    assert slack["api_url_file"] == "/etc/alertmanager/secrets/slack_webhook_url"
    assert "api_url" not in slack  # never a webhook URL in the repo
    gitignore = (STACK.parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "observability/stack/alertmanager/secrets/" in gitignore


def test_dashboard_rows_and_unique_panel_ids():
    dashboard = json.loads((STACK / "grafana" / "dashboards" / "diet-planner.json").read_text(encoding="utf-8"))
    rows = [p["title"] for p in dashboard["panels"] if p["type"] == "row"]
    assert rows[:3] == ["Overview", "SLOs & alerts (measured at the web app)", "Safety & business"]
    ids = [p["id"] for p in dashboard["panels"]]
    assert len(ids) == len(set(ids))
    assert {v["name"] for v in dashboard["templating"]["list"]} >= {"price_in", "price_out"}
