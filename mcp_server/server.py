"""MCP server: serves ALL the diet tools to agents over Streamable HTTP.

Name, address and tool descriptions come from mcp_server/config.yaml.
Each tool's logic lives in its own file under mcp_server/tools/.

Run it (in its own terminal):
    uv run python mcp_server/server.py
With telemetry:
    uv run --env-file otel.env python mcp_server/server.py
"""

import os
import sys
from pathlib import Path

# Make "mcp_server" importable when this file is started directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.telemetry import ToolMetricsMiddleware, instrument_http, setup_telemetry  # noqa: E402

# Turn on OpenTelemetry export first (only if OTEL_EXPORTER_OTLP_ENDPOINT is set).
telemetry_enabled = setup_telemetry()

from mcp.server.mcpserver import MCPServer  # noqa: E402

from mcp_server.config import SERVER, TOOLS  # noqa: E402
from mcp_server.db import ensure_database  # noqa: E402
from mcp_server.tools import build_week_menu, calculate_daily_calories, get_meal_options  # noqa: E402

# Build the meals database from the seed file on first run.
ensure_database()

HOST = os.getenv("MCP_HOST", SERVER["host"])  # Cloud Run needs 0.0.0.0
PORT = int(os.getenv("MCP_PORT", SERVER["port"]))  # MCP_PORT lets the tests use another port

mcp = MCPServer(
    name=SERVER["name"],
    instructions=SERVER["instructions"],
    middleware=[ToolMetricsMiddleware()] if telemetry_enabled else [],
)

# Register each tool with the description from the YAML file.
for tool in (calculate_daily_calories, get_meal_options, build_week_menu):
    mcp.tool(description=TOOLS[tool.__name__]["description"])(tool)


if __name__ == "__main__":
    if "--stdio" in sys.argv:  # used by the tests
        mcp.run(transport="stdio")
    else:
        import uvicorn

        from starlette.responses import JSONResponse
        from starlette.routing import Route

        app = mcp.streamable_http_app(host=HOST)

        async def info(request):  # GET / : what this is, instead of "Not Found" in a browser
            return JSONResponse({
                "service": f"MCP server: {SERVER['name']}",
                "note": "This is an API used by the orchestrator, not a web page.",
                "mcp_endpoint": f"{str(request.base_url).rstrip('/')}/mcp",
                "tools": sorted(TOOLS),
            })

        app.router.routes.append(Route("/", info, methods=["GET"]))
        if telemetry_enabled:
            instrument_http(app)  # HTTP request metrics + spans, like the agents have
        print(f"MCP server running on http://{HOST}:{PORT}/mcp")
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
