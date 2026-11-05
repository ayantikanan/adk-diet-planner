#!/usr/bin/env bash
# Prints the public link of every Cloud Run service of the project, and checks each one answers.
#   Usage:  bash deploy/show_urls.sh
# Needs gcloud, signed in with an account that can view the project's Cloud Run services.
set -euo pipefail

P=${PROJECT_ID:-eternal-nucleus-513516-a2}
R=${REGION:-us-central1}

# What to open on each service: the web pages, and the API addresses of the others.
declare -A OPEN=(
  [diet-planner]="/                                      web app (open in a browser)"
  [diet-orchestrator]="/                                      ADK developer UI (open in a browser)"
  [diet-mcp-server]="/mcp                                   MCP tools (API)"
  [diet-grocery-agent]="/a2a/grocery_agent                     A2A agent (API)"
  [diet-workout-agent]="/a2a/workout_agent                     A2A agent (API)"
)

printf "%-20s %-6s %s\n" SERVICE STATUS LINK
for service in diet-planner diet-orchestrator diet-mcp-server diet-grocery-agent diet-workout-agent; do
  url=$(gcloud run services describe "$service" --project "$P" --region "$R" --format='value(metadata.annotations."run.googleapis.com/urls")' 2>/dev/null \
        | tr -d '[]"' | cut -d, -f1)
  [[ -n "$url" ]] || url=$(gcloud run services describe "$service" --project "$P" --region "$R" --format='value(status.url)' 2>/dev/null || true)
  if [[ -z "$url" ]]; then
    printf "%-20s %-6s %s\n" "$service" "-" "not deployed"
    continue
  fi
  read -r path what <<<"${OPEN[$service]}"
  code=$(curl -s -o /dev/null -m 60 -w "%{http_code}" "$url/")  # 403 = not public, 000 = not answering
  printf "%-20s %-6s %s  (%s)\n" "$service" "$code" "${url}${path%/}" "$what"
done

health=$(curl -s -m 60 "https://diet-planner-$(gcloud projects describe "$P" --format='value(projectNumber)').$R.run.app/api/health" || true)
echo
echo "Web app health: ${health:-not answering}"
