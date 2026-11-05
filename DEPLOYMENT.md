# Deploying Diet Planner to Google Cloud (Cloud Run)

Project: `eternal-nucleus-513516-a2` (number `34656130941`), region `us-central1`.
One Docker image runs every part; the `SERVICE` env var picks which one starts (`deploy/start.py`).

There are two ways to run it on Cloud Run:

| Layout | Cloud Run services | When to use |
|---|---|---|
| **A. Separate services** (target) | `diet-planner` (web UI), `diet-orchestrator`, `diet-mcp-server`, `diet-grocery-agent`, `diet-workout-agent` | Each part in its own container, scaled on its own. Needs the IAM setup in step 1. |
| **B. All-in-one** (demo) | `diet-planner` with `SERVICE=all` | MCP server and both agents run inside the orchestrator's container on localhost. Works without extra IAM. |

## Current deployment (live)

| Cloud Run service | `SERVICE` | Runs |
|---|---|---|
| `diet-planner` | `ui` | Web UI (`ui/app.py`): https://diet-planner-34656130941.us-central1.run.app |
| `diet-orchestrator` | `orchestrator` | Orchestrator agent + ADK developer UI |
| `diet-mcp-server` | `mcp` | MCP tools (`/mcp`) |
| `diet-grocery-agent` | `grocery_agent` | Grocery A2A agent |
| `diet-workout-agent` | `workout_agent` | Workout A2A agent |

All five are public. Section 3B (all-in-one) is the fallback when the separate services can't be made public.

---

## 0. Prerequisites

- `gcloud` installed and signed in: `gcloud auth login`, then
  `gcloud config set project eternal-nucleus-513516-a2` and `gcloud config set run/region us-central1`
- APIs enabled (already done): Cloud Run, Artifact Registry, Cloud Build, Secret Manager,
  Cloud Trace, Logging, Monitoring, Telemetry.
- Artifact Registry repo `diet-planner` in `us-central1` (already exists).
- A Gemini API key: in `agents/.env` locally and in Secret Manager as `GOOGLE_API_KEY`.

## 1. One-time IAM setup (project Owner only)

An account with **Editor + Run Developer cannot do these steps**: it can deploy services but not make
them public or grant roles. The project Owner runs:

```bash
P=eternal-nucleus-513516-a2
RUN_SA=serviceAccount:34656130941-compute@developer.gserviceaccount.com

# Cloud Build can build the image; the services can read the key and write telemetry
for R in roles/cloudbuild.builds.builder roles/cloudtrace.agent roles/logging.logWriter \
         roles/monitoring.metricWriter roles/telemetry.tracesWriter; do
  gcloud projects add-iam-policy-binding $P --member=$RUN_SA --role=$R --condition=None
done
gcloud secrets add-iam-policy-binding GOOGLE_API_KEY --project $P \
  --member=$RUN_SA --role=roles/secretmanager.secretAccessor

# Optional: let a teammate make services public themselves
gcloud projects add-iam-policy-binding $P --member=user:<their-email> --role=roles/run.admin
```

## 2. Build and push the image

With Cloud Build (needs step 1):

```bash
gcloud builds submit --region us-central1 \
  --tag us-central1-docker.pkg.dev/eternal-nucleus-513516-a2/diet-planner/app:v2 .
```

Or with local Docker (no IAM needed; Docker Desktop must be running):

```bash
gcloud auth configure-docker us-central1-docker.pkg.dev
docker build -t us-central1-docker.pkg.dev/eternal-nucleus-513516-a2/diet-planner/app:v2 .
docker push us-central1-docker.pkg.dev/eternal-nucleus-513516-a2/diet-planner/app:v2
```

## 3A. Deploy the four separate services

```bash
IMAGE=us-central1-docker.pkg.dev/eternal-nucleus-513516-a2/diet-planner/app:v2
url() { echo "https://$1-34656130941.us-central1.run.app"; }   # Cloud Run's fixed URL format
KEY=GOOGLE_API_KEY=GOOGLE_API_KEY:latest                      # from Secret Manager (step 1)
COMMON="GOOGLE_CLOUD_PROJECT=eternal-nucleus-513516-a2,GOOGLE_GENAI_USE_VERTEXAI=FALSE,OTEL_TO_GCP=true"

gcloud run deploy diet-mcp-server --image $IMAGE --memory 1Gi --max 3 \
  --set-env-vars "$COMMON,SERVICE=mcp,OTEL_SERVICE_NAME=diet-mcp-server"

gcloud run deploy diet-grocery-agent --image $IMAGE --memory 1Gi --max 2 --set-secrets $KEY \
  --set-env-vars "$COMMON,SERVICE=grocery_agent,OTEL_SERVICE_NAME=diet-grocery-agent,A2A_PUBLIC_URL=$(url diet-grocery-agent)"

gcloud run deploy diet-workout-agent --image $IMAGE --memory 1Gi --max 2 --set-secrets $KEY \
  --set-env-vars "$COMMON,SERVICE=workout_agent,OTEL_SERVICE_NAME=diet-workout-agent,A2A_PUBLIC_URL=$(url diet-workout-agent)"

gcloud run deploy diet-orchestrator --image $IMAGE --memory 1Gi --max 1 --session-affinity --set-secrets $KEY \
  --set-env-vars "$COMMON,SERVICE=orchestrator,OTEL_SERVICE_NAME=diet-orchestrator,MCP_SERVER_URL=$(url diet-mcp-server)/mcp,GROCERY_AGENT_URL=$(url diet-grocery-agent)/a2a/grocery_agent,WORKOUT_AGENT_URL=$(url diet-workout-agent)/a2a/workout_agent"
```

The client-facing web app (`ui/app.py`) as its own service, talking to the orchestrator:

```bash
U=34656130941.us-central1.run.app
gcloud run deploy diet-planner --image $IMAGE --memory 512Mi --min 0 --max 3 --no-session-affinity \
  --set-env-vars "^|^SERVICE=ui|ADK_URL=https://diet-orchestrator-$U|UI_LINKS=off|UI_CHECK_TELEMETRY_COLLECTOR=off|UI_CHECK_MCP_SERVER=diet-mcp-server-$U:443|UI_CHECK_GROCERY_AGENT=https://diet-grocery-agent-$U/a2a/grocery_agent/.well-known/agent-card.json|UI_CHECK_WORKOUT_AGENT=https://diet-workout-agent-$U/a2a/workout_agent/.well-known/agent-card.json"
```

Make all five public (needs Owner or Cloud Run Admin). The web app calls the orchestrator, and the
orchestrator calls the other three, without a sign-in token, so **every** service must be public:

```bash
for s in diet-planner diet-orchestrator diet-mcp-server diet-grocery-agent diet-workout-agent; do
  gcloud run services add-iam-policy-binding $s --member=allUsers --role=roles/run.invoker
done
```

## 3B. Deploy the all-in-one demo service

```bash
gcloud run deploy diet-planner --image $IMAGE --memory 2Gi --cpu 2 --min 1 --max 1 --session-affinity \
  --update-env-vars SERVICE=all,OTEL_TO_GCP=false,GOOGLE_CLOUD_PROJECT=eternal-nucleus-513516-a2
```

`diet-planner` is already public, and a new deploy keeps it public.

## 4. Test

Open https://diet-planner-34656130941.us-central1.run.app (the web app, fill in the profile and click
**Build my plan**), the orchestrator URL (ADK developer UI) or https://diet-planner-34656130941.us-central1.run.app (B) in a browser,
pick **orchestrator**, and ask:

> I am 35, female, 75 kg, 165 cm, lightly active, want to lose weight. Not vegetarian, no allergies.

From the command line:

```bash
B=https://diet-planner-34656130941.us-central1.run.app
curl -X POST $B/apps/orchestrator/users/demo/sessions/s1 -H "Content-Type: application/json" -d '{}'
curl -X POST $B/run -H "Content-Type: application/json" -d '{"app_name":"orchestrator","user_id":"demo",
  "session_id":"s1","new_message":{"role":"user","parts":[{"text":"I am 35, female, 75 kg, 165 cm, lightly active, want to lose weight."}]}}'
```

A good answer has called `calculate_daily_calories`, `get_meal_options`, `grocery_agent` and `workout_agent`.

Quick checks for layout A:

```bash
curl https://diet-grocery-agent-34656130941.us-central1.run.app/a2a/grocery_agent/.well-known/agent-card.json
curl https://diet-orchestrator-34656130941.us-central1.run.app/list-apps
```

## 5. Automatic deploys (GitHub Actions)

`.github/workflows/deployment-cicd.yaml` does steps 2 and 3A on every push to `main`. One-time setup
(deployer service account, Workload Identity, two repo secrets) is in the README section
"Deploy to Cloud Run (GitHub Actions)". It also needs step 1 and an account allowed to make services public.

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `...compute@developer... does not have storage.objects.get` on `gcloud builds submit` | Cloud Build account has no roles | Step 1, or build with local Docker |
| `Changes to invoker_iam_disabled require run.services.setIamPolicy` / `PERMISSION_DENIED ... setIamPolicy` | Your account can't make services public | Owner runs the `add-iam-policy-binding` loop |
| Browser shows 403 on a `run.app` URL | Service is private | Same as above |
| `Session not found` | Sessions are in memory; the instance restarted or scaled to zero | Keep `--min 1` and `--session-affinity` during demos |
| Orchestrator can't reach a sub-agent | Sub-agent private, or wrong `*_URL` | Make all four public; check the env vars on `diet-orchestrator` |

**Security:** the `diet-planner` service and the three Gemini services in layout A currently have
`GOOGLE_API_KEY` as a plain env var (Secret Manager wasn't readable yet). After step 1, switch them to
`--set-secrets GOOGLE_API_KEY=GOOGLE_API_KEY:latest` and rotate the key.

## Telemetry to local Grafana

Without Cloud Trace permissions, the Cloud Run services can send their traces, metrics and logs to
the **local** observability stack instead, through a public, token-protected endpoint:

```
Cloud Run services --OTLP/HTTPS + bearer token--> https://<random>.trycloudflare.com  (Cloudflare tunnel)
   --> local OTel Collector :4319 (rejects requests without the token)
   --> Tempo + Jaeger, Prometheus, Loki --> Grafana http://localhost:3000
```

1. Once: a secret in `observability/stack/.env` (git-ignored):
   `TELEMETRY_TOKEN=<output of: python -c "import secrets;print(secrets.token_hex(32))">`
2. Start the stack with the tunnel (Docker Desktop must be running):
   ```bash
   docker compose -f observability/stack/docker-compose.yml -f observability/stack/docker-compose.cloud.yml up -d
   ```
3. Find the endpoint address:
   ```bash
   docker logs stack-tunnel-1 2>&1 | grep -o "https://[a-z0-9-]*\.trycloudflare\.com"
   ```
4. Point the 5 services at it (checks the endpoint first):
   ```bash
   bash deploy/connect_telemetry.sh https://<random>.trycloudflare.com
   ```
5. Build a plan, then open Grafana http://localhost:3000 (dashboard "Diet Planner - Agents, MCP & LLM")
   or Jaeger http://localhost:16686. The web app's Telemetry dot is green while the endpoint is up.

The tunnel address changes whenever the tunnel restarts: repeat steps 3 and 4. Telemetry only flows while
this machine and Docker are running (the services keep working when it's off; they just drop telemetry).
Stop sending: `bash deploy/connect_telemetry.sh --off`.
