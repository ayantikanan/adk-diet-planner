# Diet Planner - multi-agent system with Google ADK, MCP, A2A and OpenTelemetry

An **orchestrator agent** builds a one-day diet plan. It gets exact numbers from an
**MCP tool server**, delegates work to **two sub-agents over A2A**, and every step is
traced with **OpenTelemetry** and shown in **Grafana** and **Jaeger**.

Ask: *"I'm 35, female, 75 kg, 165 cm, lightly active, want to lose weight."*
Get back: a meal plan with calories and macros, a shopping list, and a workout.

---

## Architecture

```
  Browser (ADK web UI) :8000
          |
          v
  +--------------------+          Gemini (decides what to call next)
  |   orchestrator     | <------>
  |  agents/orchestrator
  +--------------------+
     |  MCP (Streamable HTTP)          |  A2A (HTTP, JSON-RPC)
     v                                 v
  +----------------------+     +-------------------------------+
  | MCP server  :8002    |     | A2A server  :8001             |
  |  calculate_daily_    |     |  grocery_agent  (Gemini)      |
  |    calories          |     |  workout_agent  (Gemini +     |
  |  get_meal_options    |     |    estimate_calories_burned)  |
  |    (SQLite database) |     +-------------------------------+
  +----------------------+

  every service --OTLP :4318--> OTel Collector --> Tempo + Jaeger (traces)
                                               --> Prometheus      (metrics)
                                               --> Loki            (logs)
                                                       --> Grafana :3000
```

| Service | Folder | Port | What it does |
|---|---|---|---|
| Orchestrator | `agents/orchestrator/` | 8000 | The agent users talk to. Calls the MCP tools and the two sub-agents, then writes the final plan. |
| Grocery agent | `agents/grocery_agent/` | 8001 | A2A sub-agent: turns the chosen dishes into a shopping list. |
| Workout agent | `agents/workout_agent/` | 8001 | A2A sub-agent: suggests a workout and calculates calories burned with its own tool. |
| MCP server | `mcp_server/` | 8002 | Tool server: calorie and macro calculator, meal lookup from SQLite. |
| Observability | `observability/stack/` | 3000, 16686, 9090 | Collector, Tempo, Jaeger, Prometheus, Loki and Grafana in Docker. |

**Tech stack:** Google ADK, Gemini, MCP Python SDK (Streamable HTTP), A2A, SQLite,
OpenTelemetry, Grafana, Tempo, Jaeger, Prometheus, Loki, Docker, uv, pytest, ADK evals.

---

## Project structure

```
diet-planner/
|-- agents/                       one folder per agent (the ADK agents directory)
|   |-- .env                      GOOGLE_API_KEY (never committed; copy .env.example)
|   |-- orchestrator/
|   |   |-- agent.py              MCP tools + 2 A2A sub-agents
|   |   |-- config.yaml           prompt, models, MCP + sub-agent addresses
|   |   |-- fallback_model.py     tries the next Gemini model when one is busy
|   |   `-- telemetry.py          sends the agent's logs to OpenTelemetry
|   |-- grocery_agent/
|   |   |-- agent.py
|   |   |-- agent.json            A2A agent card
|   |   |-- config.yaml           prompt + model
|   |   `-- telemetry.py
|   `-- workout_agent/
|       |-- agent.py
|       |-- agent.json            A2A agent card
|       |-- config.yaml           prompt, model, tool description, MET values
|       |-- tools.py              estimate_calories_burned
|       `-- telemetry.py
|-- mcp_server/
|   |-- server.py                 starts the MCP server (Streamable HTTP)
|   |-- config.yaml               address, tool descriptions, formula numbers
|   |-- config.py, db.py          settings loader, SQLite data layer
|   |-- telemetry.py              traces, metrics, logs + tool-call metrics
|   |-- tools/                    calorie_tool.py, meal_tool.py
|   `-- data/                     schema.sql, seed_meals.json (diet.db is built from them)
|-- observability/
|   |-- otel_launch.py            runs `adk` with HTTP tracing + metrics switched on
|   |-- verify_traces.py          checks one trace covers MCP and A2A calls
|   `-- stack/                    docker-compose.yml, collector, Tempo, Prometheus, Grafana
|-- tests/                        pytest suite (no API key needed)
|-- otel.env                      OpenTelemetry settings
`-- pyproject.toml, uv.lock       dependencies (uv)
```

Every component reads only its own `config.yaml`, so prompts and settings change
without touching code.

---

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Python 3.12, Docker Desktop, and a
Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).

```powershell
git clone https://github.com/ayantikanan/adk-diet-planner.git
cd adk-diet-planner
uv sync
Copy-Item agents\.env.example agents\.env     # then put your key in agents\.env
```

---

## Run (4 terminals, from the project folder)

```powershell
# 1. Observability stack (Docker Desktop must say "Engine running")
docker compose -f observability/stack/docker-compose.yml up -d

# 2. MCP server (port 8002)
$env:OTEL_SERVICE_NAME="diet-mcp-server"; uv run --env-file otel.env python mcp_server/server.py

# 3. A2A sub-agents (port 8001)
$env:OTEL_SERVICE_NAME="diet-a2a-agents"; uv run --env-file otel.env python observability/otel_launch.py api_server --a2a --port 8001 agents

# 4. Orchestrator + ADK web UI (port 8000) - start this last
$env:OTEL_SERVICE_NAME="diet-orchestrator"; uv run --env-file otel.env python observability/otel_launch.py web --port 8000 agents
```

Open **http://localhost:8000** and pick **orchestrator**.

| URL | What |
|---|---|
| http://localhost:8000 | ADK web UI (chat with the agents) |
| http://localhost:3000 | Grafana dashboard: **Diet Planner - Agents, MCP & LLM** |
| http://localhost:16686 | Jaeger (traces) |
| http://localhost:9090 | Prometheus (raw metrics) |
| http://localhost:8001/a2a/grocery_agent/.wel
---

## Web app (client-facing)

A branded page for end users, instead of the ADK developer UI.

```powershell
# 5. Web app (port 8080) - after the orchestrator is running
uv run python ui/app.py
```

Open **http://127.0.0.1:8080**, fill in the profile and click **Build my plan**.

| Part of the page | What it shows |
|---|---|
| Status bar | Green or red dot for the planner, MCP tools, both A2A agents and telemetry (`/api/health`) |
| Grafana dashboard / Jaeger traces | Links to the dashboard and the trace search |
| Daily Targets label | Calories and macros from the `calculate_daily_calories` MCP tool |
| Plan | Meal table, shopping list and workout written by the orchestrator |
| Agent activity | Each MCP tool call and A2A hand-off live, with timing and output |
| Open this run in Jaeger | The full trace of that exact request across all services |

`ui/app.py` (FastAPI) serves the page and relays the orchestrator's live events
(`/run_sse`), so the browser never talks to the agents directly. Settings, including the
Grafana and Jaeger addresses, are in `ui/config.yaml`.
