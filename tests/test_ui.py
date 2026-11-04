"""Tests for the web app (ui/app.py). No agents need to be running."""

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

APP_PATH = Path(__file__).resolve().parents[1] / "ui" / "app.py"
spec = importlib.util.spec_from_file_location("ui_app", APP_PATH)
ui_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui_app)
client = TestClient(ui_app.app)


def test_page_and_assets_are_served():
    page = client.get("/")
    assert page.status_code == 200
    assert "Build my plan" in page.text
    for asset in ("/static/app.js", "/static/styles.css"):
        assert client.get(asset).status_code == 200


def test_health_reports_every_service():
    health = client.get("/api/health").json()
    assert set(health) == {"orchestrator", "tools", "grocery_agent", "workout_agent", "telemetry"}
    assert all(isinstance(up, bool) for up in health.values())


def test_links_point_to_grafana_and_jaeger():
    links = client.get("/api/links").json()
    assert "/d/diet-planner" in links["dashboard"]
    assert "{trace_id}" in links["trace"]


def test_chat_needs_a_session():
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 422


def test_memory_endpoints_reject_bad_ids():
    assert client.get("/api/users/../../etc/history").status_code in (400, 404)
    assert client.get("/api/users/not-a-web-id/memory").status_code == 400
    assert client.get("/api/users/web-abcdef123456/history/bad id!").status_code == 400
    assert client.delete("/api/users/robert'); DROP TABLE").status_code in (400, 404, 405)


def test_answer_detection_for_the_sli():
    answer = 'data: {"author": "orchestrator", "content": {"parts": [{"text": "Here is your plan"}]}}'
    question = 'data: {"author": "user", "content": {"parts": [{"text": "hi"}]}}'
    tool_call = 'data: {"author": "orchestrator", "content": {"parts": [{"functionCall": {"name": "x"}}]}}'
    assert ui_app._has_answer(ui_app._event(answer))
    assert not ui_app._has_answer(ui_app._event(question))
    assert not ui_app._has_answer(ui_app._event(tool_call))
    assert ui_app._event("data: not json") == {}


def test_cloud_trace_links_on_google_cloud(monkeypatch):
    monkeypatch.setenv("CLOUD_TRACE_PROJECT", "demo-project")
    spec_gcp = importlib.util.spec_from_file_location("ui_app_gcp", APP_PATH)
    app_gcp = importlib.util.module_from_spec(spec_gcp)
    spec_gcp.loader.exec_module(app_gcp)
    links = TestClient(app_gcp.app).get("/api/links").json()
    assert links["cloud_trace"].endswith("project=demo-project")
    assert links["cloud_trace_run"].endswith("&tid={trace_id}")
    assert "cloud_trace" not in client.get("/api/links").json()  # not on the laptop


def test_cloud_trace_only_mode(monkeypatch):
    """OTEL_TO_GCP=true (gcp.env): links go to Cloud Trace only, telemetry counts as up."""
    monkeypatch.setenv("OTEL_TO_GCP", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.delenv("CLOUD_TRACE_PROJECT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app", lambda *a, **k: None)
    monkeypatch.setattr("web_telemetry.setup", lambda app: False)
    spec_gcp = importlib.util.spec_from_file_location("ui_app_gcp_only", APP_PATH)
    app_gcp = importlib.util.module_from_spec(spec_gcp)
    spec_gcp.loader.exec_module(app_gcp)
    gcp_client = TestClient(app_gcp.app)
    links = gcp_client.get("/api/links").json()
    assert set(links) == {"cloud_trace", "cloud_trace_run"}
    assert gcp_client.get("/api/health").json()["telemetry"] is True


def test_cloud_trace_and_grafana_mode(monkeypatch):
    """gcp.env + otel.env: Cloud Trace links and the Grafana / Jaeger links."""
    monkeypatch.setenv("OTEL_TO_GCP", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.delenv("CLOUD_TRACE_PROJECT", raising=False)
    monkeypatch.setattr("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app", lambda *a, **k: None)
    monkeypatch.setattr("web_telemetry.setup", lambda app: False)
    spec_both = importlib.util.spec_from_file_location("ui_app_gcp_and_grafana", APP_PATH)
    app_both = importlib.util.module_from_spec(spec_both)
    spec_both.loader.exec_module(app_both)
    links = TestClient(app_both.app).get("/api/links").json()
    assert {"dashboard", "traces", "cloud_trace", "cloud_trace_run"} <= set(links)
