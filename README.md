# Diet Planner - multi-agent system with Google ADK, MCP, A2A and OpenTelemetry

[![CI](https://github.com/ayantikanan/adk-diet-planner/actions/workflows/ci.yml/badge.svg)](https://github.com/ayantikanan/adk-diet-planner/actions/workflows/ci.yml)

An **orchestrator agent** builds a one-day diet plan. It gets exact numbers from an
**MCP tool server**, delegates work to **two sub-agents over A2A**, and every step is
traced with **OpenTelemetry** and shown in **Grafana** and **Jaeger**.

Ask: *"I'm 35, female, 75 kg, 165 cm, lightly active, want to lose weight."*
Get back: a meal plan with calories and macros, a shopping list, and a workout.

## Live demo (Google Cloud Run)

| What | Link |
|---|---|
| Web app | https://diet-planner-34656130941.us-central1.run.app |
| ADK developer UI (pick **orchestrator**) | https://diet-orchestrator-34656130941.us-central1.run.app |
| Health check | https://diet-planner-34656130941.us-central1.run.app/api/health |

Each part runs as its own Cloud Run service (web app, orchestrator, MCP server, two A2A agents).
To list every link and check it answers: `bash deploy/show_urls.sh`. Step-by-step deployment,
where to find the links in the Cloud Console, and troubleshooting: [DEPLOYMENT.md](DEPLOYMENT.md).

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
     |  MCP (Streamable HTTP)          |  A2A (HTTP, JSON-RPC), one server per agent
     v                                 v
  +----------------------+     +-------------------------------+
  | MCP server  :8002    |     | grocery_agent  :8001          |
  |  calculate_daily_    |     |  (Gemini)                     |
  |    calories          |     +-------------------------------+
  |  get_meal_options    |     +-------------------------------+
  |    (SQLite database) |     | workout_agent  :8003          |
  +----------------------+     |  (Gemini +                    |
                               |    estimate_calories_burned)  |
                               +-------------------------------+

  every service --OTLP :4318--> OTel Collector --> Tempo + Jaeger (traces)
                                               --> Prometheus      (metrics)
                                               --> Loki            (logs)
                                                       --> Grafana :3000
```

| Service | Folder | Port | What it does |
|---|---|---|---|
| Orchestrator | `agents/orchestrator/` | 8000 | The agent users talk to. Calls the MCP tools and the two sub-agents, then writes the final plan. |
| Grocery agent | `agents/grocery_agent/` | 8001 | A2A sub-agent on its own server: turns the chosen dishes into a shopping list. |
| Workout agent | `agents/workout_agent/` | 8003 | A2A sub-agent on its own server: suggests a workout and calculates calories burned with its own tool. |
| MCP server | `mcp_server/` | 8002 | Tool server: calorie and macro calculator, meal lookup from SQLite. |
| Observability | `observability/stack/` | 3000, 16686, 9090 | Collector, Tempo, Jaeger, Prometheus, Loki and Grafana in Docker. |

**Tech stack:** Google ADK, Gemini, MCP Python SDK (Streamable HTTP), A2A, SQLite,
OpenTelemetry, Grafana, Tempo, Jaeger, Prometheus, Loki, Docker, uv, pytest, ADK evals.

---

## Project structure

```
diet-planner/
|-- agents/                       one folder per agent (the ADK agents directory)
|   |-- .env.example              template: copy to .env and add GOOGLE_API_KEY (.env is never committed)
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
|-- serve_agent.py                serves one A2A agent on its own server (own process and port)
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

## Run (5 terminals, from the project folder)

```powershell
# 1. Observability stack (Docker Desktop must say "Engine running")
docker compose -f observability/stack/docker-compose.yml up -d

# 2. MCP server (port 8002)
$env:OTEL_SERVICE_NAME="diet-mcp-server"; uv run --env-file otel.env python mcp_server/server.py

# 3. Grocery agent, on its own server (port 8001)
uv run --env-file otel.env python serve_agent.py grocery_agent --port 8001

# 4. Workout agent, on its own server (port 8003)
uv run --env-file otel.env python serve_agent.py workout_agent --port 8003

# 5. Orchestrator + ADK web UI (port 8000) - start this last
$env:OTEL_SERVICE_NAME="diet-orchestrator"; uv run --env-file otel.env python observability/otel_launch.py web --port 8000 agents
```

Open **http://localhost:8000** and pick **orchestrator**.

| URL | What |
|---|---|
| http://localhost:8000 | ADK web UI (chat with the agents) |
| http://localhost:3000 | Grafana dashboard: **Diet Planner - Agents, MCP & LLM** |
| http://localhost:16686 | Jaeger (traces) |
| http://localhost:9090 | Prometheus (raw metrics) |
| http://localhost:8001/a2a/grocery_agent/.well-known/agent-card.json | Grocery agent's A2A card (its own server) |
| http://localhost:8003/a2a/workout_agent/.well-known/agent-card.json | Workout agent's A2A card (its own server) |
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

---

## Memory

- **Remembers you:** after a plan, the orchestrator saves the profile in ADK `user:` state
  (`agents/orchestrator/memory.py`, an `after_tool_callback`). The instruction reads it back with
  `{user:profile_text?}`, so a returning user can just say "make me a plan".
- **Past plans:** the web app lists earlier conversations; clicking one replays it (plan, targets and
  every agent step) and the conversation can continue.
- **Survives restarts:** conversations are stored in SQLite at `agents/orchestrator/.adk/session.db`.
- **Forget me:** deletes the saved profile and every past conversation.

---

## More features

| Feature | How it works |
|---|---|
| Allergies and dislikes | 9 allergen chips and a dislikes box. Meals carry allergens and ingredients in SQLite (`meal_allergens` table); `get_meal_options` filters them in one parameterised query. Remembered with the profile. |
| Plan my week | MCP tool `build_week_menu`: 7 days, each close to the calorie target (portions 0.75x-1.5x), fewest repeats, same filters. Day tabs in the web app; 7-day shopping list from the grocery agent. |
| Swap a meal | A Swap button on each row of the daily plan; the agent picks a dish with similar calories and updates the shopping list. |
| Print or save as PDF | A print layout with just the Daily Targets label and the latest plan (all 7 days for a week). |

---

## Observability: SLOs, alerts, exemplars

- **Traces start at the click.** The web app is instrumented too (`ui/web_telemetry.py`, service
  `diet-web-app`): one trace covers web app -> orchestrator -> MCP server -> A2A agents.
- **User-facing SLIs**, measured in the web app: `diet_plan_requests_total` (by kind: day, week, swap,
  followup; outcome: success, error) and `diet_plan_duration_seconds` (click to answer).
- **SLOs:** 99% of plans succeed; 90% finish within 60 s. Grafana row "SLOs & alerts" shows success,
  speed, error budget left, burn rate and the alerts firing now.
- **Alerts as code:** `observability/stack/prometheus/diet-planner.rules.yml` (multi-window burn-rate
  alert, slow plans, Gemini errors, error spans, MCP tool errors, guardrail spike, collector down),
  unit-tested with `promtool test rules` in CI. See them at http://localhost:9090/alerts.
- **Exemplars:** the dots on latency graphs link to the exact trace in Tempo.

Run the web app with tracing (PowerShell):

    $env:OTEL_SERVICE_NAME="diet-web-app"; uv run --env-file otel.env python ui/app.py

---

## Google Cloud, alert notifications and business metrics

- **Cloud Shell:** the web app's Grafana and Jaeger links switch to the Web Preview addresses
  automatically. Start the stack with the Google Cloud add-on to also send traces and logs to
  **Cloud Trace** and **Cloud Logging** (metrics stay in Prometheus):

      docker compose -f observability/stack/docker-compose.yml -f observability/stack/docker-compose.gcp.yml up -d

  and run the web app with `CLOUD_TRACE_PROJECT=<project-id>` to get "Open this run in Cloud Trace".
- **Alert notifications:** Prometheus sends firing alerts to **Alertmanager** (http://localhost:9093),
  which posts them to Slack. Put the Slack incoming-webhook URL in
  `observability/stack/alertmanager/secrets/slack_webhook_url` (git-ignored).
- **Safety & business row** in Grafana: guardrail blocks by rule, allergens avoided, plans by type,
  estimated Gemini cost (prices editable at the top of the dashboard) and tokens per plan.

---

## Google Cloud observability and business metrics

- **Cloud Shell:** the web app's Grafana and Jaeger links switch to the Web Preview addresses
  automatically. Start the stack with the Google Cloud add-on to also send traces and logs to
  **Cloud Trace** and **Cloud Logging** (metrics stay in Prometheus):

      docker compose -f observability/stack/docker-compose.yml -f observability/stack/docker-compose.gcp.yml up -d

  and run the web app with `CLOUD_TRACE_PROJECT=<project-id>` to get "Open this run in Cloud Trace".
- **Safety & business row** in Grafana: guardrail blocks by rule, allergens avoided, plans by type,
  estimated Gemini cost (prices editable at the top of the dashboard) and tokens per plan.

---

## Cloud Trace only (Google Cloud, no Docker)

On Google Cloud the services can skip the local stack and send traces, logs and metrics straight to
**Cloud Trace**, **Cloud Logging** and **Cloud Monitoring** (through `telemetry.googleapis.com`):
use `gcp.env` instead of `otel.env`, and add `--otel_to_cloud` to the orchestrator and both agent (`serve_agent.py`) commands.
The web app then links to Cloud Trace only ("Open this run in Cloud Trace"). Set `OTEL_GCP_METRICS=false`
in `gcp.env` if your project can't take custom metrics.

**Both Google Cloud and Grafana:** start the local stack without the add-on (no Google sign-in needed)
and load both files, so every service also sends to the OTel Collector:

    docker compose -f observability/stack/docker-compose.yml up -d
    uv run --env-file gcp.env --env-file otel.env python serve_agent.py grocery_agent --port 8001 --otel_to_cloud

The web app then shows the Grafana and Jaeger links as well as "Open this run in Cloud Trace".

## Deploy to Cloud Run (GitHub Actions)

[`.github/workflows/deployment-cicd.yaml`](.github/workflows/deployment-cicd.yaml) runs the tests, builds one
image and deploys it as **four separate public Cloud Run services**, each in its own container:

| Cloud Run service | `SERVICE` | What it is |
|---|---|---|
| `diet-orchestrator` | `orchestrator` | ADK web UI, open this URL in a browser |
| `diet-mcp-server` | `mcp` | MCP tool server (`/mcp`) |
| `diet-grocery-agent` | `grocery_agent` | A2A sub-agent (`/a2a/grocery_agent`) |
| `diet-workout-agent` | `workout_agent` | A2A sub-agent (`/a2a/workout_agent`) |

It runs on every push to `main` that changes the app, or by hand from the Actions tab ("Run workflow").
The orchestrator finds the others through `MCP_SERVER_URL`, `GROCERY_AGENT_URL` and `WORKOUT_AGENT_URL`;
the Gemini key comes from the `GOOGLE_API_KEY` secret in Secret Manager, and telemetry goes to Cloud Trace,
Logging and Monitoring (same settings as `gcp.env`).

**One-time setup** (in Cloud Shell or any terminal signed in as a project owner):

```bash
P=eternal-nucleus-513516-a2; N=34656130941; REPO=ayantikanan/adk-diet-planner

# 1. Runtime account (the services run as it): read the Gemini key, write telemetry
RUN_SA=34656130941-compute@developer.gserviceaccount.com
for R in roles/cloudtrace.agent roles/logging.logWriter roles/monitoring.metricWriter roles/telemetry.tracesWriter; do
  gcloud projects add-iam-policy-binding $P --member=serviceAccount:$RUN_SA --role=$R --condition=None
done
gcloud secrets add-iam-policy-binding GOOGLE_API_KEY --project $P \
  --member=serviceAccount:$RUN_SA --role=roles/secretmanager.secretAccessor

# 2. Deployer account (GitHub Actions signs in as it): push images, deploy Cloud Run
gcloud iam service-accounts create github-deployer --project $P
DEPLOY_SA=github-deployer@$P.iam.gserviceaccount.com
for R in roles/run.admin roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding $P --member=serviceAccount:$DEPLOY_SA --role=$R --condition=None
done
gcloud iam service-accounts add-iam-policy-binding $RUN_SA --project $P \
  --member=serviceAccount:$DEPLOY_SA --role=roles/iam.serviceAccountUser

# 3. Workload Identity Federation: only this GitHub repo may sign in as the deployer (no key file)
gcloud iam workload-identity-pools create github --project $P --location=global
gcloud iam workload-identity-pools providers create-oidc github --project $P --location=global \
  --workload-identity-pool=github --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository \
  --attribute-condition="assertion.repository=='$REPO'"
gcloud iam service-accounts add-iam-policy-binding $DEPLOY_SA --project $P \
  --role=roles/iam.workloadIdentityUser \
  --member=principalSet://iam.googleapis.com/projects/$N/locations/global/workloadIdentityPools/github/attribute.repository/$REPO
```

Then add two **repository secrets** in GitHub (Settings > Secrets and variables > Actions):

| Secret | Value |
|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/34656130941/locations/global/workloadIdentityPools/github/providers/github` |
| `GCP_DEPLOYER_SERVICE_ACCOUNT` | `github-deployer@eternal-nucleus-513516-a2.iam.gserviceaccount.com` |

After a run, the job summary lists the four URLs. Cloud Run sessions are in memory, so the orchestrator
runs as one instance with session affinity; a restart starts new chats.


Secrets (API key, telemetry token, Slack webhook) stay in git-ignored files; each has a committed
`.example` template. See [DEPLOYMENT.md](DEPLOYMENT.md#secrets).
