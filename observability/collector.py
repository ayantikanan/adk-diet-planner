"""A tiny local OpenTelemetry collector (OTLP over HTTP).

It receives traces, metrics and logs from every process (orchestrator,
A2A agents, MCP server) and saves them as JSON lines in telemetry/:
    telemetry/traces.jsonl   telemetry/metrics.jsonl   telemetry/logs.jsonl

Run it (in its own terminal):
    uv run python observability/collector.py
"""

import gzip
import json
import os
from pathlib import Path

import uvicorn
from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

OUT_DIR = Path(os.getenv("TELEMETRY_DIR", Path(__file__).resolve().parent.parent / "telemetry"))
OUT_DIR.mkdir(exist_ok=True)

SIGNALS = {
    "traces": (ExportTraceServiceRequest, ExportTraceServiceResponse, "resourceSpans"),
    "metrics": (ExportMetricsServiceRequest, ExportMetricsServiceResponse, "resourceMetrics"),
    "logs": (ExportLogsServiceRequest, ExportLogsServiceResponse, "resourceLogs"),
}


def make_handler(signal: str):
    request_type, response_type, key = SIGNALS[signal]

    async def handler(request: Request) -> Response:
        body = await request.body()
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        message = request_type()
        message.ParseFromString(body)
        data = MessageToDict(message)
        with open(OUT_DIR / f"{signal}.jsonl", "a", encoding="utf-8") as f:
            for item in data.get(key, []):
                f.write(json.dumps(item) + "\n")
        count = len(data.get(key, []))
        print(f"[collector] received {signal} batch ({count} resource group(s))", flush=True)
        return Response(
            response_type().SerializeToString(), media_type="application/x-protobuf"
        )

    return handler


app = Starlette(
    routes=[Route(f"/v1/{signal}", make_handler(signal), methods=["POST"]) for signal in SIGNALS]
)

if __name__ == "__main__":
    print(f"[collector] OTLP/HTTP listening on http://127.0.0.1:4318, writing to {OUT_DIR}")
    uvicorn.run(app, host="127.0.0.1", port=4318, log_level="warning")
