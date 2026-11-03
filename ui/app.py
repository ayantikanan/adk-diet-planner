"""Web app for the diet planner: serves the UI and talks to the orchestrator.

The browser never calls the agents directly. This small backend:
  - serves the page (ui/static/)
  - creates a conversation session on the orchestrator
  - streams the orchestrator's live events (tool calls, A2A hand-offs, answer)
  - reports the health of every service

Settings come from ui/config.yaml.

Run it (in its own terminal):
    uv run python ui/app.py
Then open http://127.0.0.1:8080
"""

import asyncio
import json
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


class ChatRequest(BaseModel):
    session_id: str
    user_id: str
    message: str


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/sessions")
async def create_session() -> dict:
    """Starts a new conversation with the orchestrator."""
    user_id = f"web-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(f"{ADK_URL}/apps/{APP_NAME}/users/{user_id}/sessions", json={})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(503, f"The orchestrator isn't reachable at {ADK_URL}: {e}") from e
    return {"session_id": r.json()["id"], "user_id": user_id}


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
