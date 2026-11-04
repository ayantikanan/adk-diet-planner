"""Tests the observability pieces (no API key or running servers needed)."""

import importlib
import json
from types import SimpleNamespace

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from starlette.testclient import TestClient

from mcp_server.telemetry import ToolMetricsMiddleware


def test_collector_saves_traces(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRY_DIR", str(tmp_path))
    import observability.collector as collector

    importlib.reload(collector)  # pick up TELEMETRY_DIR

    request = ExportTraceServiceRequest(resource_spans=[ResourceSpans(
        resource={"attributes": [KeyValue(key="service.name", value=AnyValue(string_value="test-svc"))]},
        scope_spans=[ScopeSpans(spans=[Span(name="invoke_agent orchestrator", trace_id=b"1" * 16, span_id=b"2" * 8)])],
    )])
    response = TestClient(collector.app).post(
        "/v1/traces", content=request.SerializeToString(),
        headers={"content-type": "application/x-protobuf"},
    )

    assert response.status_code == 200
    saved = json.loads((tmp_path / "traces.jsonl").read_text().splitlines()[0])
    assert saved["scopeSpans"][0]["spans"][0]["name"] == "invoke_agent orchestrator"


async def test_mcp_tool_metrics_are_recorded():
    reader = InMemoryMetricReader()
    middleware = ToolMetricsMiddleware(MeterProvider(metric_readers=[reader]))

    async def call_next(ctx):
        return "ok"

    ctx = SimpleNamespace(method="tools/call", params={"name": "calculate_daily_calories"})
    assert await middleware(ctx, call_next) == "ok"

    metrics = {
        m.name: m
        for rm in reader.get_metrics_data().resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
    }
    point = metrics["diet_mcp.tool.calls"].data.data_points[0]
    assert point.value == 1
    assert dict(point.attributes) == {"tool.name": "calculate_daily_calories", "status": "ok"}
    assert "diet_mcp.tool.duration" in metrics


def test_mcp_exports_to_google_cloud_and_grafana(monkeypatch):
    """gcp.env + otel.env: the MCP server sends to Google Cloud and to the OTel Collector."""
    from google.adk.telemetry import google_cloud
    from google.adk.telemetry.setup import OTelHooks

    from mcp_server import telemetry

    gcp_span, gcp_reader, gcp_log = object(), object(), object()
    monkeypatch.setattr(google_cloud, "get_gcp_exporters", lambda **kw: OTelHooks(
        span_processors=[gcp_span], metric_readers=[gcp_reader], log_record_processors=[gcp_log]))
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    spans, readers, logs = telemetry._exporters(to_gcp=True)
    assert (spans, readers, logs) == ([gcp_span], [gcp_reader], [gcp_log])  # Cloud Trace only

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    spans, readers, logs = telemetry._exporters(to_gcp=True)
    assert spans[0] is gcp_span and len(spans) == 2
    assert readers[0] is gcp_reader and len(readers) == 2
    assert logs[0] is gcp_log and len(logs) == 2
    for reader in readers[1:]:
        reader.shutdown()
