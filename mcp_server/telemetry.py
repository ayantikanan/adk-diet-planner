"""OpenTelemetry for the MCP server process: traces, metrics and logs.

The MCP SDK already creates a span for every request ("tools/call <tool>") and
continues the caller's trace (ADK sends the trace context with each call).
This module only switches on exporting, adds tool metrics, and sends Python
logs to OpenTelemetry. It does nothing unless OTEL_EXPORTER_OTLP_ENDPOINT is set
(local: the OTel Collector) or OTEL_TO_GCP=true (Google Cloud Trace, Monitoring
and Logging, using ADK's Google Cloud exporters).
"""

import logging
import os
import time
from typing import Any

from opentelemetry import _logs, metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger("diet_mcp_server")

# Longer than the default buckets (which stop at 10 s), same as the agents use.
DURATION_BUCKETS_S = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10,
                      20, 30, 45, 60, 90, 120, 180, 300)


def _exporters(to_gcp: bool) -> tuple[list, list, list]:
    """Returns (span processors, metric readers, log processors) for the backend."""
    if to_gcp:
        from google.adk.telemetry.google_cloud import get_gcp_exporters

        hooks = get_gcp_exporters(
            enable_cloud_tracing=True, enable_cloud_metrics=True, enable_cloud_logging=True
        )
        return hooks.span_processors, hooks.metric_readers, hooks.log_record_processors
    return (
        [BatchSpanProcessor(OTLPSpanExporter())],
        [PeriodicExportingMetricReader(OTLPMetricExporter())],
        [BatchLogRecordProcessor(OTLPLogExporter())],
    )


def setup_telemetry() -> bool:
    """Sets up export for traces, metrics and logs. Returns True if enabled."""
    to_gcp = os.getenv("OTEL_TO_GCP", "").lower() == "true"
    if not (to_gcp or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")):
        return False

    resource = Resource.create(
        {"service.name": os.getenv("OTEL_SERVICE_NAME", "diet-mcp-server")}
    )
    span_processors, metric_readers, log_processors = _exporters(to_gcp)

    tracer_provider = TracerProvider(resource=resource)
    for processor in span_processors:
        tracer_provider.add_span_processor(processor)
    trace.set_tracer_provider(tracer_provider)

    metrics.set_meter_provider(
        MeterProvider(
            resource=resource,
            metric_readers=metric_readers,
            views=[
                View(
                    instrument_name="http.server.request.duration",
                    aggregation=ExplicitBucketHistogramAggregation(DURATION_BUCKETS_S),
                )
            ],
        )
    )

    logger_provider = LoggerProvider(resource=resource)
    for processor in log_processors:
        logger_provider.add_log_record_processor(processor)
    _logs.set_logger_provider(logger_provider)
    logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))
    logger.setLevel(logging.INFO)

    logger.info("MCP server telemetry enabled (%s)", "Google Cloud" if to_gcp else "OTLP collector")
    return True


def instrument_http(app: Any) -> None:
    """Adds an HTTP server span and http.server.request.duration to every request.

    The span continues the caller's trace (traceparent header), and the
    per-chunk "http send/receive" spans of streamed responses are skipped.
    """
    from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

    app.add_middleware(OpenTelemetryMiddleware, exclude_spans=["receive", "send"])


class ToolMetricsMiddleware:
    """Counts MCP tool calls and records how long each one takes."""

    def __init__(self, meter_provider: Any = None) -> None:
        meter = (meter_provider or metrics.get_meter_provider()).get_meter("diet_mcp_server")
        self.calls = meter.create_counter(
            "diet_mcp.tool.calls", description="Number of MCP tool calls"
        )
        self.duration = meter.create_histogram(
            "diet_mcp.tool.duration", unit="ms", description="MCP tool call duration"
        )

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        if ctx.method != "tools/call":
            return await call_next(ctx)

        tool = (ctx.params or {}).get("name", "unknown")
        start = time.perf_counter()
        status = "ok"
        try:
            return await call_next(ctx)
        except Exception:
            status = "error"
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            attributes = {"tool.name": tool, "status": status}
            self.calls.add(1, attributes)
            self.duration.record(elapsed_ms, attributes)
            logger.info("tool call %s finished (%s) in %.1f ms", tool, status, elapsed_ms)
