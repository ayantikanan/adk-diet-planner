"""Web app for the diet planner: serves the UI and talks to the orchestrator.

The browser never calls the agents directly. This small backend:
  - serves the page (ui/static/)
  - creates a conversation session on the orchestrator
  - streams the orchestrator's live events (tool calls, A2A hand-offs, answer)
  - reports the health of every service

It also gives each browser a lasting user id, so the orchestrator can
remember the user's profile and list their past plans (memory).

Settings come from ui/config.yaml.

Run it (in its own terminal):
    uv run python ui/app.py
Then open http://127.0.0.1:8080
"""

import asyncio
import json
import re
import uuid
from pathlib import Path

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

UI = yaml.safe_load((Path(__file__).resolve().parent / "config.yaml").read_text(encoding="utf-8"))
CHECKS = UI["health_checks"]
ADK_URL = UI["adk_url"].rstrip("/")
APP_NAME = UI["app_name"]
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Diet planner")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


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

    async def relay():
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
            try:
                async with client.stream("POST", f"{ADK_URL}/run_sse", json=body) as response:
                    if response.status_code != 200:
                        detail = (await response.aread()).decode(errors="replace")[:300]
                        error = f"Orchestrator returned {response.status_code}: {detail}"
                        yield f"data: {json.dumps({'error': error})}\n\n"
                        return
                    async for line in response.aiter_lines():
                        if line:
                            yield line + "\n\n"
            except httpx.HTTPError as e:
                error = f"Lost connection to the orchestrator ({type(e).__name__})."
                yield f"data: {json.dumps({'error': error})}\n\n"

    return StreamingResponse(relay(), media_type="text/event-stream")


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
    async with httpx.AsyncClient(timeout=2) as client:
        orchestrator, grocery, workout = await asyncio.gather(
            _http_ok(client, f"{ADK_URL}/list-apps"),
            _http_ok(client, CHECKS["grocery_agent"]),
            _http_ok(client, CHECKS["workout_agent"]),
        )
    tools, telemetry = await asyncio.gather(
        _port_open(CHECKS["mcp_server"]),
        _port_open(CHECKS["telemetry_collector"]),
    )
    return {
        "orchestrator": orchestrator,
        "tools": tools,
        "grocery_agent": grocery,
        "workout_agent": workout,
        "telemetry": telemetry,
    }


LINKS = UI.get("links", {})


@app.get("/api/links")
async def links() -> dict:
    """Grafana and Jaeger addresses from ui/config.yaml."""
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
    return {"trace_id": trace_id, "url": LINKS["trace"].format(trace_id=trace_id)}


if __name__ == "__main__":
    print(f"Diet planner web app: http://{UI['host']}:{UI['port']}")
    uvicorn.run(app, host=UI["host"], port=UI["port"], log_level="warning")
