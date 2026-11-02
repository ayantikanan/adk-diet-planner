"""Exports this agent's Python logs as OpenTelemetry logs.

ADK already exports traces and metrics when the OTEL_* environment variables
are set (see otel.env), or to Google Cloud when OTEL_TO_GCP=true; this adds
ADK's log messages (and ours) to that.
"""

import logging
import os


def export_logs() -> None:
    if not (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_TO_GCP")):
        return
    from opentelemetry._logs import get_logger_provider
    from opentelemetry.sdk._logs import LoggingHandler

    adk_logger = logging.getLogger("google_adk")
    if not any(isinstance(h, LoggingHandler) for h in adk_logger.handlers):
        adk_logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=get_logger_provider()))
