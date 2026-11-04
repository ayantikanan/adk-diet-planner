"""Serves ONE A2A agent on its own server (its own process and port).

    uv run --env-file otel.env python serve_agent.py grocery_agent --port 8001
    uv run --env-file otel.env python serve_agent.py workout_agent --port 8003

Each agent gets the same URLs as with `adk api_server --a2a` (/a2a/<agent>/ and
its card at /a2a/<agent>/.well-known/agent-card.json), so only the port differs.
Separate servers mean one agent can be restarted, scaled or can crash without
touching the other.

Telemetry, like observability/otel_launch.py does for the `adk` commands:
  - otel.env:  traces, metrics and logs to the local OTel Collector
  - gcp.env + --otel_to_cloud:  straight to Cloud Trace, Cloud Logging and Cloud Monitoring
Incoming requests continue the orchestrator's trace (traceparent header), so each
agent's spans still appear inside the one trace of the plan.
"""

import argparse
import logging
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\]")  # ADK's A2A notices, once per class

AGENTS_DIR = Path(__file__).resolve().parent / "agents"
DURATION_BUCKETS_S = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10,
                      20, 30, 45, 60, 90, 120, 180, 300)


def setup_telemetry(to_gcp: bool) -> None:
    """Traces, metrics and logs for this agent (service.name from OTEL_SERVICE_NAME)."""
    if not (to_gcp or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")):
        return
    from google.adk.telemetry.setup import OTelHooks, maybe_set_otel_providers
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create()
    hooks = OTelHooks()
    if to_gcp:
        import google.auth
        from google.adk.telemetry.google_cloud import get_gcp_exporters, get_gcp_resource

        _, project_id = google.auth.default()
        resource = get_gcp_resource(project_id).merge(resource)
        hooks = get_gcp_exporters(
            enable_cloud_tracing=True,
            enable_cloud_logging=True,
            enable_cloud_metrics=os.getenv("OTEL_GCP_METRICS", "true").lower() == "true",
        )
        readers = hooks.metric_readers
    else:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        readers = [PeriodicExportingMetricReader(OTLPMetricExporter())]

    # Our own meter provider first, for the longer duration buckets (as in otel_launch.py).
    metrics.set_meter_provider(MeterProvider(
        resource=resource,
        metric_readers=readers,
        views=[View(instrument_name=name, aggregation=ExplicitBucketHistogramAggregation(DURATION_BUCKETS_S))
               for name in ("http.server.request.duration", "http.client.request.duration")],
    ))
    # Traces and logs: ADK's own setup (adds the OTLP exporters from the OTEL_* variables).
    # It also offers a second meter provider, which OTel refuses with a warning: expected.
    logging.getLogger("opentelemetry.metrics._internal").setLevel(logging.ERROR)
    maybe_set_otel_providers(
        otel_hooks_to_setup=[OTelHooks(span_processors=hooks.span_processors,
                                       log_record_processors=hooks.log_record_processors)],
        otel_resource=resource,
    )


def build_app(name: str, host: str = "127.0.0.1", port: int = 8001):
    """The A2A app for agents/<name>: JSON-RPC and its card under /a2a/<name>."""
    import json
    from importlib import import_module

    from google.adk.a2a.utils.agent_to_a2a import to_a2a
    from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

    card_path = AGENTS_DIR / name / "agent.json"
    if not card_path.is_file():
        sys.exit(f"{card_path.parent} has no agent.json: it isn't an A2A agent.")
    card = json.loads(card_path.read_text(encoding="utf-8"))
    if str(port) not in card["url"]:
        logging.warning("%s says %s, but this server is on port %s: the orchestrator "
                        "will look for the agent at the card's address.", card_path, card["url"], port)

    if str(AGENTS_DIR) not in sys.path:
        sys.path.insert(0, str(AGENTS_DIR))
    agent = import_module(f"{name}.agent").root_agent
    # ADK warns that the card's url isn't rewritten for rpc_path: it already has /a2a/<name>.
    adk_logger = logging.getLogger("google_adk")
    quiet = lambda record: "Both agent_card and rpc_path" not in record.getMessage()
    adk_logger.addFilter(quiet)
    try:
        app = to_a2a(agent, host=host, port=port, rpc_path=f"a2a/{name}", agent_card=str(card_path))
    finally:
        adk_logger.removeFilter(quiet)
    app.add_middleware(OpenTelemetryMiddleware, exclude_spans=["receive", "send"])  # continues the caller's trace
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("agent", help="folder name under agents/, e.g. grocery_agent")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--otel_to_cloud", action="store_true", help="send telemetry to Google Cloud")
    args = parser.parse_args()

    from google.adk.cli.utils.envs import load_dotenv_for_agent

    load_dotenv_for_agent(args.agent, str(AGENTS_DIR))  # agents/.env (GOOGLE_API_KEY)
    os.environ.setdefault("OTEL_SERVICE_NAME", f"diet-{args.agent.replace('_', '-')}")
    setup_telemetry(args.otel_to_cloud)

    # The agent is imported only now (in build_app), so its loggers find the telemetry providers.
    import uvicorn
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()
    app = build_app(args.agent, args.host, args.port)
    print(f"A2A agent {args.agent}: http://{args.host}:{args.port}/a2a/{args.agent}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
