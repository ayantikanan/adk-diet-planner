"""Runs an `adk` CLI command with OpenTelemetry switched on for HTTP traffic.

Usage (anything after the script name is passed to `adk`):
    uv run --env-file otel.env python observability/otel_launch.py web --port 8000 agents
    uv run --env-file otel.env python observability/otel_launch.py api_server --port 8000 agents
(the A2A sub-agents have their own launcher: serve_agent.py)

It is exactly `adk <args>`, plus:
  - FastAPI (server): continues the caller's trace on incoming A2A requests and
    records http.server.request.duration for every route
  - HTTPX (client):   sends the trace context on outgoing A2A and MCP requests
  - a meter provider with longer duration buckets: the default ones stop at
    10 s, but an agent request takes up to a minute, so every p95 would read 10 s.
    ADK sees a meter provider is already set and uses this one.
ADK itself sets up the trace and log exporters from the OTEL_* environment variables,
or, with `--otel_to_cloud` (used on Google Cloud), exports everything to Cloud Trace,
Cloud Monitoring and Cloud Logging; the metrics then go through the same views here.
"""

import os
import sys

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource

DURATION_BUCKETS_S = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10,
                      20, 30, 45, 60, 90, 120, 180, 300)

TO_GCP = "--otel_to_cloud" in sys.argv


def _metric_readers() -> list:
    if TO_GCP:
        from google.adk.telemetry.google_cloud import get_gcp_exporters

        return get_gcp_exporters(enable_cloud_metrics=True).metric_readers
    return [PeriodicExportingMetricReader(OTLPMetricExporter())]


if TO_GCP or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    metrics.set_meter_provider(
        MeterProvider(
            resource=Resource.create(),  # service.name comes from OTEL_SERVICE_NAME
            metric_readers=_metric_readers(),
            views=[
                View(instrument_name=name, aggregation=ExplicitBucketHistogramAggregation(DURATION_BUCKETS_S))
                for name in ("http.server.request.duration", "http.client.request.duration")
            ],
        )
    )

FastAPIInstrumentor().instrument()
HTTPXClientInstrumentor().instrument()

from google.adk.cli.cli_tools_click import main  # noqa: E402

sys.argv = ["adk", *sys.argv[1:]]
sys.exit(main())
