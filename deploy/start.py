"""Starts one service inside the container. Cloud Run runs this for every service.

Which service is chosen with the SERVICE environment variable:
    SERVICE=mcp           the MCP tool server
    SERVICE=a2a           the A2A sub-agents (grocery_agent, workout_agent)
    SERVICE=orchestrator  the orchestrator + ADK web UI
    SERVICE=all           all three in one container: MCP server and A2A agents in
                          the background on localhost, the orchestrator on PORT
                          (used when the services can't call each other publicly)

Cloud Run tells the container which port to listen on with PORT (default 8080).
With OTEL_TO_GCP=true, traces, metrics and logs go to Google Cloud Trace,
Cloud Monitoring and Cloud Logging.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE = os.getenv("SERVICE", "")
PORT = os.getenv("PORT", "8080")
TO_GCP = os.getenv("OTEL_TO_GCP", "").lower() == "true"


def set_agent_card_urls(public_url: str) -> None:
    """Points each A2A agent card at this service's public address.

    The cards in the repo say http://localhost:8001; on Cloud Run the orchestrator
    must be told the real https://...run.app address instead.
    """
    for card_path in sorted((ROOT / "agents").glob("*/agent.json")):
        card = json.loads(card_path.read_text(encoding="utf-8"))
        card["url"] = f"{public_url.rstrip('/')}/a2a/{card['name']}"
        card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")
        print(f"A2A card {card['name']}: {card['url']}", flush=True)


def adk_command(*args: str) -> list[str]:
    command = [sys.executable, str(ROOT / "observability" / "otel_launch.py"), *args,
               "--host", "0.0.0.0", "--port", PORT,
               "--session_service_uri", "memory://", "--artifact_service_uri", "memory://"]
    if TO_GCP:
        command.append("--otel_to_cloud")
    return command + [str(ROOT / "agents")]


def start_background_services() -> None:
    """Starts the MCP server (:8002) and A2A agents (:8001) inside this container."""
    os.environ.setdefault("MCP_SERVER_URL", "http://127.0.0.1:8002/mcp")
    os.environ.setdefault("GROCERY_AGENT_URL", "http://127.0.0.1:8001/a2a/grocery_agent")
    os.environ.setdefault("WORKOUT_AGENT_URL", "http://127.0.0.1:8001/a2a/workout_agent")
    set_agent_card_urls("http://127.0.0.1:8001")
    for name, port in (("mcp", "8002"), ("a2a", "8001")):
        env = {**os.environ, "SERVICE": name, "PORT": port,
               "OTEL_SERVICE_NAME": f"diet-{'mcp-server' if name == 'mcp' else 'a2a-agents'}"}
        subprocess.Popen([sys.executable, str(Path(__file__).resolve())], env=env)
        print(f"Started {name} in the background on port {port}", flush=True)


def main() -> None:
    if SERVICE == "mcp":
        os.environ.update(MCP_HOST="0.0.0.0", MCP_PORT=PORT)
        command = [sys.executable, str(ROOT / "mcp_server" / "server.py")]
    elif SERVICE == "a2a":
        if public_url := os.getenv("A2A_PUBLIC_URL"):
            set_agent_card_urls(public_url)
        command = adk_command("api_server", "--a2a")
    elif SERVICE == "orchestrator":
        command = adk_command("web")
    elif SERVICE == "all":
        start_background_services()
        command = adk_command("web")
    else:
        sys.exit("Set SERVICE to one of: mcp, a2a, orchestrator, all")

    print(f"Starting {SERVICE} on port {PORT}: {' '.join(command)}", flush=True)
    os.chdir(ROOT)
    os.execv(command[0], command)  # replace this process, so Cloud Run's signals reach the server


if __name__ == "__main__":
    main()
