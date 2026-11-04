"""Checks the collected telemetry and prints the latest conversation's trace.

Run after sending a message in the ADK web UI:
    uv run python observability/verify_traces.py          # readable tree
    uv run python observability/verify_traces.py --all    # every span
"""

import base64
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

TELEMETRY = Path(__file__).resolve().parent.parent / "telemetry"

ORCHESTRATOR = "diet-orchestrator"
GROCERY_AGENT = "diet-grocery-agent"   # serve_agent.py, port 8001
WORKOUT_AGENT = "diet-workout-agent"   # serve_agent.py, port 8003
MCP_SERVER = "diet-mcp-server"


def to_hex(value: str) -> str:
    return base64.b64decode(value).hex() if value else ""


def service_of(resource: dict) -> str:
    for attr in resource.get("attributes", []):
        if attr["key"] == "service.name":
            return attr["value"].get("stringValue", "?")
    return "?"


def read_jsonl(name: str) -> list[dict]:
    path = TELEMETRY / f"{name}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_spans() -> list[dict]:
    spans = []
    for group in read_jsonl("traces"):
        service = service_of(group.get("resource", {}))
        for scope in group.get("scopeSpans", []):
            for span in scope.get("spans", []):
                spans.append(
                    {
                        "service": service,
                        "name": span["name"],
                        "trace": to_hex(span["traceId"]),
                        "id": to_hex(span["spanId"]),
                        "parent": to_hex(span.get("parentSpanId", "")),
                        "start": int(span.get("startTimeUnixNano", 0)),
                        "ms": (int(span.get("endTimeUnixNano", 0)) - int(span.get("startTimeUnixNano", 0))) / 1e6,
                    }
                )
    return spans


# Internal plumbing spans hidden from the readable tree (still exported; see --all).
NOISE = (" http send", " http receive", "a2a.server.events.", "a2a.server.tasks.",
         "a2a.server.request_handlers.", "a2a.server.agent_execution.")


def is_noise(span: dict) -> bool:
    return any(marker in span["name"] for marker in NOISE)


def print_tree(spans: list[dict], show_all: bool = False) -> None:
    by_id = {s["id"]: s for s in spans}
    visible = spans if show_all else [s for s in spans if not is_noise(s)]
    visible_ids = {s["id"] for s in visible}

    def visible_parent(span: dict):
        parent = span["parent"]
        while parent and parent not in visible_ids:
            parent = by_id[parent]["parent"] if parent in by_id else None
        return parent or None

    children = defaultdict(list)
    for s in sorted(visible, key=lambda s: s["start"]):
        children[visible_parent(s)].append(s)

    def walk(parent, depth):
        for s in children[parent]:
            print(f"  {'  ' * depth}- [{s['service']}] {s['name']}  ({s['ms']:.0f} ms)")
            walk(s["id"], depth + 1)

    walk(None, 0)


def main() -> int:
    spans = load_spans()
    if not spans:
        print("No traces found in telemetry/traces.jsonl. Is the collector running, "
              "and were the servers started with --env-file otel.env?")
        return 1

    # The latest conversation = the latest trace that includes the orchestrator agent.
    by_trace = defaultdict(list)
    for s in spans:
        by_trace[s["trace"]].append(s)
    candidates = [t for t, ss in by_trace.items()
                  if any(s["name"].startswith("invoke_agent orchestrator") for s in ss)]
    if not candidates:
        print("No orchestrator trace yet. Send a message in the ADK web UI first.")
        return 1
    trace_id = max(candidates, key=lambda t: max(s["start"] for s in by_trace[t]))
    trace = by_trace[trace_id]

    show_all = "--all" in sys.argv
    hidden = 0 if show_all else sum(is_noise(s) for s in trace)
    print(f"\nLatest conversation trace: {trace_id}  ({len(trace)} spans"
          f"{f', {hidden} internal spans hidden - use --all to show' if hidden else ''})\n")
    print_tree(trace, show_all)

    def has(service: str, prefix: str) -> bool:
        return any(s["service"] == service and s["name"].startswith(prefix) for s in trace)

    checks = [
        ("Orchestrator agent span", has(ORCHESTRATOR, "invoke_agent orchestrator")),
        ("Gemini call spans (generate_content)", has(ORCHESTRATOR, "generate_content")),
        ("MCP tool call - orchestrator side", has(ORCHESTRATOR, "execute_tool calculate_daily_calories")),
        ("MCP tool call - MCP server side (same trace)", has(MCP_SERVER, "tools/call calculate_daily_calories")),
        ("MCP meal tool - MCP server side (same trace)", has(MCP_SERVER, "tools/call get_meal_options")),
        ("A2A call to grocery_agent - orchestrator side", has(ORCHESTRATOR, "execute_tool grocery_agent")),
        ("A2A grocery_agent - remote side (same trace)", has(GROCERY_AGENT, "invoke_agent grocery_agent")),
        ("A2A call to workout_agent - orchestrator side", has(ORCHESTRATOR, "execute_tool workout_agent")),
        ("A2A workout_agent - remote side (same trace)", has(WORKOUT_AGENT, "invoke_agent workout_agent")),
        ("Remote agent's own tool (estimate_calories_burned)", has(WORKOUT_AGENT, "execute_tool estimate_calories_burned")),
    ]
    print("\nTrace coverage checks:")
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")

    metric_names = defaultdict(set)
    for group in read_jsonl("metrics"):
        service = service_of(group.get("resource", {}))
        for scope in group.get("scopeMetrics", []):
            for metric in scope.get("metrics", []):
                metric_names[service].add(metric["name"])
    print("\nMetrics received:")
    for service in sorted(metric_names):
        print(f"  [{service}] {', '.join(sorted(metric_names[service]))}")
    if not metric_names:
        print("  (none yet - metrics are exported every 10 seconds)")

    log_counts = Counter()
    for group in read_jsonl("logs"):
        service = service_of(group.get("resource", {}))
        for scope in group.get("scopeLogs", []):
            log_counts[service] += len(scope.get("logRecords", []))
    print("\nLog records received:")
    for service, count in sorted(log_counts.items()):
        print(f"  [{service}] {count}")
    if not log_counts:
        print("  (none yet)")

    passed = all(ok for _, ok in checks)
    print(f"\nRESULT: {'ALL TRACE CHECKS PASSED' if passed else 'SOME CHECKS FAILED'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
