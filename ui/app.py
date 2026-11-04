"""Web app for the diet planner: serves the UI and talks to the orchestrator.

The browser never calls the agents directly. This small backend:
  - serves the page (ui/static/)
  - creates a conversation session on the orchestrator
  - streams the orchestrator's live events (tool calls, A2A hand-offs, answer)
  - reports the health of every service

It also gives each browser a lasting user id, so the orchestrator can
remember the user's profile and list their past plans (memory).

Settings come from ui/config.yaml.

Run it (in its own terminal), with tracing:
    $env:OTEL_SERVICE_NAME="diet-web-app"; uv run --env-file otel.env python ui/app.py
or without tracing:
    uv run python ui/app.py
Then open http://127.0.0.1:8080
"""

import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from web_telemetry import PlanTimer, untraced  # noqa: E402
from web_telemetry import setup as setup_telemetry  # noqa: E402

UI = yaml.safe_load((Path(__file__).resolve().parent / "config.yaml").read_text(encoding="utf-8"))
# On Cloud Run, environment variables override config.yaml:
#   ADK_URL                    the orchestrator's address (https://...run.app)
#   UI_CHECK_MCP_SERVER, UI_CHECK_GROCERY_AGENT, UI_CHECK_WORKOUT_AGENT, UI_CHECK_TELEMETRY_COLLECTOR
#     "same-as-orchestrator"  the service runs inside the orchestrator's container (SERVICE=all)
#     "off"                    not checked: the page hides that status dot
#     anything else            the address to check, as in config.yaml
#   UI_LINKS=off               no Grafana / Jaeger links (they only exist on a local machine)
CHECKS = {name: os.getenv(f"UI_CHECK_{name.upper()}", value) for name, value in UI["health_checks"].items()}
ADK_URL = os.getenv("ADK_URL", UI["adk_url"]).rstrip("/")
APP_NAME = UI["app_name"]
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Diet planner")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
TELEMETRY = setup_telemetry(app)  # traces, metrics and logs when OTEL_EXPORTER_OTLP_ENDPOINT is set


USER_ID = re.compile(r"^web-[a-z0-9]{8,32}$")
SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
MEMORY_KEYS = ("user:profile", "user:vegetarian", "user:targets", "user:profile_text", "user:allergies", "user:dislikes")


def _valid(pattern: re.Pattern, value: str, what: str) -> str:
    if not pattern.match(value or ""):
        raise HTTPException(400, f"Invalid {what}.")
    return value


class SessionRequest(BaseModel):
    user_id: str | None = None


class ChatRequest(BaseModel):
    session_id: str
    user_id: str
    message: str


async def _adk(method: str, path: str, **kwargs) -> httpx.Response:
    """Calls the orchestrator's ADK API (path starts with /apps/...)."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.request(method, f"{ADK_URL}{path}", **kwargs)
        except httpx.HTTPError as e:
            raise HTTPException(503, f"The orchestrator isn't reachable at {ADK_URL}: {e}") from e
    if r.status_code >= 400 and r.status_code != 404:
        raise HTTPException(502, f"Orchestrator returned {r.status_code}.")
    return r


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/sessions")
async def create_session(req: SessionRequest | None = None) -> dict:
    """Starts a new conversation. Keeps the browser's user id so memory carries over."""
    user_id = req.user_id if req and req.user_id and USER_ID.match(req.user_id) else f"web-{uuid.uuid4().hex[:12]}"
    r = await _adk("POST", f"/apps/{APP_NAME}/users/{user_id}/sessions", json={})
    return {"session_id": r.json()["id"], "user_id": user_id}


# ---------- Memory: profile and past plans ----------
async def _sessions(user_id: str) -> list[dict]:
    r = await _adk("GET", f"/apps/{APP_NAME}/users/{user_id}/sessions")
    sessions = r.json() if r.status_code == 200 else []
    return sorted(sessions, key=lambda s: s.get("lastUpdateTime") or 0, reverse=True)


def _summary(session: dict) -> dict | None:
    """Title, date and calorie target of one conversation (None if it has no message)."""
    title, kcal = None, None
    for event in session.get("events") or []:
        for part in (event.get("content") or {}).get("parts") or []:
            if title is None and event.get("author") == "user" and part.get("text"):
                title = part["text"].strip()
            response = part.get("functionResponse") or {}
            if response.get("name") == "calculate_daily_calories":
                for item in (response.get("response") or {}).get("content") or []:
                    try:
                        kcal = json.loads(item.get("text", "")).get("daily_calories", kcal)
                    except (AttributeError, ValueError):
                        pass
    if title is None:
        return None
    if len(title) > 90:
        title = title[:87].rstrip() + "..."
    return {"session_id": session["id"], "updated": session.get("lastUpdateTime"), "title": title, "kcal": kcal}


@app.get("/api/users/{user_id}/memory")
async def memory(user_id: str) -> dict:
    """What the orchestrator remembers about this user (their last profile)."""
    _valid(USER_ID, user_id, "user id")
    for session in await _sessions(user_id):
        state = session.get("state") or {}
        if state.get("user:profile"):
            return {
                "profile": state["user:profile"],
                "vegetarian": state.get("user:vegetarian"),
                "targets": state.get("user:targets"),
                "allergies": state.get("user:allergies"),
                "dislikes": state.get("user:dislikes"),
            }
    return {}


@app.get("/api/users/{user_id}/history")
async def history(user_id: str) -> list[dict]:
    """The user's past conversations, newest first (up to 10)."""
    _valid(USER_ID, user_id, "user id")
    summaries = []
    for session in (await _sessions(user_id))[:10]:
        r = await _adk("GET", f"/apps/{APP_NAME}/users/{user_id}/sessions/{session['id']}")
        if r.status_code == 200 and (summary := _summary(r.json())):
            summaries.append(summary)
    return summaries


@app.get("/api/users/{user_id}/history/{session_id}")
async def past_conversation(user_id: str, session_id: str) -> dict:
    """All events of one past conversation, so the page can replay it."""
    _valid(USER_ID, user_id, "user id")
    _valid(SESSION_ID, session_id, "session id")
    r = await _adk("GET", f"/apps/{APP_NAME}/users/{user_id}/sessions/{session_id}")
    if r.status_code == 404:
        raise HTTPException(404, "That conversation no longer exists.")
    return {"session_id": session_id, "events": r.json().get("events") or []}


@app.delete("/api/users/{user_id}")
async def forget(user_id: str) -> dict:
    """Forget me: clears the remembered profile and deletes every past conversation."""
    _valid(USER_ID, user_id, "user id")
    base = f"/apps/{APP_NAME}/users/{user_id}/sessions"
    scratch = (await _adk("POST", base, json={})).json()["id"]
    await _adk("PATCH", f"{base}/{scratch}", json={"state_delta": {key: None for key in MEMORY_KEYS}})
    sessions = await _sessions(user_id)
    for session in sessions:
        await _adk("DELETE", f"{base}/{session['id']}")
    return {"deleted_conversations": len([s for s in sessions if s["id"] != scratch])}


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Sends a message and streams the orchestrator's events back (server-sent events)."""
    body = {
        "app_name": APP_NAME,
        "user_id": req.user_id,
        "session_id": req.session_id,
        "new_message": {"role": "user", "parts": [{"text": req.message}]},
        "streaming": False,
    }

    timer = PlanTimer(req.message)  # user-facing SLI: outcome and duration of this plan

    async def relay():
        answered = False
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
            try:
                async with client.stream("POST", f"{ADK_URL}/run_sse", json=body) as response:
                    if response.status_code != 200:
                        detail = (await response.aread()).decode(errors="replace")[:300]
                        error = f"Orchestrator returned {response.status_code}: {detail}"
                        timer.finish("error", error)
                        yield f"data: {json.dumps({'error': error})}\n\n"
                        return
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        event = _event(line)
                        if event.get("error") or event.get("errorMessage"):
                            timer.finish("error", str(event.get("error") or event.get("errorMessage")))
                        elif _has_answer(event):
                            answered = True
                        yield line + "\n\n"
                timer.finish("success" if answered else "error", "finished without an answer")
            except httpx.HTTPError as e:
                error = f"Lost connection to the orchestrator ({type(e).__name__})."
                timer.finish("error", error)
                yield f"data: {json.dumps({'error': error})}\n\n"

    return StreamingResponse(relay(), media_type="text/event-stream")


def _event(line: str) -> dict:
    if not line.startswith("data:"):
        return {}
    try:
        return json.loads(line[5:])
    except ValueError:
        return {}


def _has_answer(event: dict) -> bool:
    if event.get("author") == "user":
        return False
    parts = (event.get("content") or {}).get("parts") or []
    return any(p.get("text") and not p.get("thought") for p in parts)


async def _http_ok(client: httpx.AsyncClient, url: str) -> bool:
    try:
        return (await client.get(url)).status_code < 500
    except httpx.HTTPError:
        return False


async def _port_open(address: str) -> bool:
    host, port = address.rsplit(":", 1)
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)), timeout=1.5)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


@app.get("/api/health")
async def health() -> dict:
    """Checks each service the planner depends on."""
    async def check(name: str, probe) -> bool | None:
        address = CHECKS[name]
        if address == "off":
            return None
        if address == "same-as-orchestrator":
            return None  # filled in below
        return await probe(address)

    with untraced():  # polled every 10 s: keep it out of the traces
        async with httpx.AsyncClient(timeout=3) as client:
            http_ok = lambda url: _http_ok(client, url)  # noqa: E731
            orchestrator, grocery, workout, tools, telemetry = await asyncio.gather(
                _http_ok(client, f"{ADK_URL}/list-apps"),
                check("grocery_agent", http_ok),
                check("workout_agent", http_ok),
                check("mcp_server", _port_open),
                check("telemetry_collector", _port_open),
            )
    result = {
        "orchestrator": orchestrator,
        "tools": tools,
        "grocery_agent": grocery,
        "workout_agent": workout,
        "telemetry": telemetry or TO_GCP,  # Cloud Trace only mode: no collector to check
    }
    for key, name in (("tools", "mcp_server"), ("grocery_agent", "grocery_agent"),
                      ("workout_agent", "workout_agent"), ("telemetry", "telemetry_collector")):
        if CHECKS[name] == "same-as-orchestrator":
            result[key] = orchestrator
        elif CHECKS[name] == "off" and not (key == "telemetry" and TO_GCP):
            result[key] = None
    return result


# Cloud Trace only mode (OTEL_TO_GCP=true, see gcp.env): no Grafana or Jaeger, so no links to them.
# With otel.env as well (OTEL_EXPORTER_OTLP_ENDPOINT), Grafana and Jaeger run too: keep the links.
TO_GCP = os.getenv("OTEL_TO_GCP", "").lower() == "true"
LINKS = {} if (TO_GCP and not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")) or os.getenv("UI_LINKS") == "off" \
    else dict(UI.get("links", {}))
# Traces also (or only) in Cloud Trace: link to them. CLOUD_TRACE_PROJECT is the project id.
_cloud_project = os.getenv("CLOUD_TRACE_PROJECT") or (os.getenv("GOOGLE_CLOUD_PROJECT") if TO_GCP else None)
if _cloud_project:
    _console = f"https://console.cloud.google.com/traces/list?project={_cloud_project}"
    LINKS["cloud_trace"] = _console
    LINKS["cloud_trace_run"] = _console + "&tid={trace_id}"


@app.get("/api/links")
async def links() -> dict:
    """Grafana and Jaeger addresses from ui/config.yaml (plus Cloud Trace on Google Cloud)."""
    return LINKS


@app.get("/api/trace/{session_id}")
async def latest_trace(session_id: str) -> dict:
    """Jaeger link for the latest request in this conversation."""
    _valid(SESSION_ID, session_id, "session id")
    url = f"{ADK_URL}/dev/apps/{APP_NAME}/debug/trace/session/{session_id}"
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            r = await client.get(url)
            spans = r.json() if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            spans = []
    if not spans:
        return {"url": LINKS.get("traces")}
    latest = max(spans, key=lambda s: s.get("start_time") or 0)
    trace_id = f"{int(latest['trace_id']):032x}"
    result = {"trace_id": trace_id}
    if "trace" in LINKS:
        result["url"] = LINKS["trace"].format(trace_id=trace_id)
    if "cloud_trace_run" in LINKS:
        result["cloud_url"] = LINKS["cloud_trace_run"].format(trace_id=trace_id)
    return result


if __name__ == "__main__":
    host = os.getenv("UI_HOST", UI["host"])  # Cloud Run: 0.0.0.0 and its PORT
    port = int(os.getenv("PORT", UI["port"]))
    print(f"Diet planner web app: http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
