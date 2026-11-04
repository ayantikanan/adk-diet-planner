"""OpenTelemetry for the web app (ui/app.py): traces, metrics and logs.

With this on, every plan's trace starts at the web app ("POST /api/chat" in the
diet-web-app service) and continues through the orchestrator, the MCP server
and the A2A agents, because the trace context is sent with each call.

It also measures what the user experiences, for the SLOs in Grafana:
  diet.plan.requests  counter    every plan request, by kind and outcome
  diet.plan.duration  histogram  seconds from click to finished answer, by kind
Kinds: day, week, swap, followup. Outcomes: success, error.

Exports to the local OTel Collector when OTEL_EXPORTER_OTLP_ENDPOINT is set
(otel.env), straight to Cloud Trace, Cloud Logging and Cloud Monitoring when
OTEL_TO_GCP=true (gcp.env), or to both with both files. Otherwise nothing happens.
"""

import logging
import os
import re
import time
from contextlib import contextmanager

from opentelemetry import metrics, trace
from opentelemetry.instrumentation.utils import suppress_instrumentation

logger = logging.getLogger("diet_web_app")

# Same long buckets as the other services: a plan can take a minute or more.
DURATION_BUCKETS_S = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10,
                      20, 30, 45, 60, 90, 120, 180, 300)
# Requests that would only add noise to the traces (the page polls /api/health every 10 s).
EXCLUDED_URLS = "api/health,api/links,api/trace,static,favicon"
KINDS = ("day", "week", "swap", "followup")
OUTCOMES = ("success", "error")

meter = metrics.get_meter("diet_web_app")
plan_requests = meter.create_counter(
    "diet.plan.requests", description="Plan requests from the web app, by kind and outcome"
)
plan_duration = meter.create_histogram(
    "diet.plan.duration", unit="s", description="Time from the user's click to the finished answer"
)


def setup(app) -> bool:
    """Switches on export and instruments the FastAPI app. Returns True if enabled."""
    to_gcp = os.getenv("OTEL_TO_GCP", "").lower() == "true"
    if not (to_gcp or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")):
        return False
    from opentelemetry import _logs
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "diet-web-app")})
    if to_gcp:  # straight to Google Cloud, using ADK's exporters (telemetry.googleapis.com)
        import google.auth
        from google.adk.telemetry.google_cloud import get_gcp_exporters, get_gcp_resource

        _, project_id = google.auth.default()
        resource = get_gcp_resource(project_id).merge(resource)
        hooks = get_gcp_exporters(
            enable_cloud_tracing=True,
            enable_cloud_logging=True,
            enable_cloud_metrics=os.getenv("OTEL_GCP_METRICS", "true").lower() == "true",
        )
        span_processors, metric_readers, log_processors = (
            list(hooks.span_processors), list(hooks.metric_readers), list(hooks.log_record_processors))
    else:
        span_processors, metric_readers, log_processors = [], [], []
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):  # the OTel Collector (Grafana); with gcp.env too: both
        span_processors.append(BatchSpanProcessor(OTLPSpanExporter()))
        metric_readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
        log_processors.append(BatchLogRecordProcessor(OTLPLogExporter()))

    tracer_provider = TracerProvider(resource=resource)
    for processor in span_processors:
        tracer_provider.add_span_processor(processor)
    trace.set_tracer_provider(tracer_provider)

    metrics.set_meter_provider(
        MeterProvider(
            resource=resource,
            metric_readers=metric_readers,
            views=[
                View(instrument_name=name, aggregation=ExplicitBucketHistogramAggregation(DURATION_BUCKETS_S))
                for name in ("http.server.request.duration", "http.client.request.duration", "diet.plan.duration")
            ],
        )
    )

    logger_provider = LoggerProvider(resource=resource)
    for processor in log_processors:
        logger_provider.add_log_record_processor(processor)
    _logs.set_logger_provider(logger_provider)
    logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))
    logger.setLevel(logging.INFO)

    # Start every series at 0, so Prometheus' rate() sees the very first error too.
    for kind in KINDS:
        for outcome in OUTCOMES:
            plan_requests.add(0, {"kind": kind, "outcome": outcome})

    FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS)
    HTTPXClientInstrumentor().instrument()  # sends the trace context to the orchestrator
    return True


@contextmanager
def untraced():
    """For background calls (health checks) that shouldn't create traces."""
    with suppress_instrumentation():
        yield


def request_kind(message: str) -> str:
    if re.search(r"\bPlan my whole week\b", message):
        return "week"
    if message.startswith("Swap my "):
        return "swap"
    if message.startswith("I'm ") and " kg, " in message:
        return "day"
    return "followup"


class PlanTimer:
    """Measures one plan request: call .finish(outcome) when the answer is done."""

    def __init__(self, message: str):
        self.kind = request_kind(message)
        self.started = time.perf_counter()
        self.finished = False
        self.span = trace.get_current_span()  # the "POST /api/chat" span
        self.span.set_attribute("diet.request.kind", self.kind)

    def finish(self, outcome: str, detail: str = "") -> None:
        if self.finished:
            return
        self.finished = True
        seconds = time.perf_counter() - self.started
        attributes = {"kind": self.kind, "outcome": outcome}
        plan_requests.add(1, attributes)
        plan_duration.record(seconds, attributes)
        self.span.set_attribute("diet.plan.outcome", outcome)
        if outcome == "error":
            self.span.set_status(trace.Status(trace.StatusCode.ERROR, detail[:200]))
            logger.warning("plan request failed (%s, %.1f s): %s", self.kind, seconds, detail[:200])
        else:
            logger.info("plan request finished (%s, %.1f s)", self.kind, seconds)
