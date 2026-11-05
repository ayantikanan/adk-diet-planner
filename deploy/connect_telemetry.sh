#!/usr/bin/env bash
# Points the 5 Cloud Run services at the telemetry endpoint of the local stack
# (observability/stack/docker-compose.cloud.yml), so their traces, metrics and logs show up
# in the local Grafana, Tempo, Jaeger, Prometheus and Loki.
#   Usage:  bash deploy/connect_telemetry.sh https://<random>.trycloudflare.com
#   Undo:   bash deploy/connect_telemetry.sh --off
# Run it again whenever the tunnel restarts (its address changes).
set -euo pipefail
cd "$(dirname "$0")/.."

P=eternal-nucleus-513516-a2; R=us-central1
BACKENDS=(diet-mcp-server diet-grocery-agent diet-workout-agent diet-orchestrator)
OTEL_VARS=OTEL_EXPORTER_OTLP_ENDPOINT,OTEL_EXPORTER_OTLP_HEADERS,OTEL_EXPORTER_OTLP_PROTOCOL,OTEL_METRIC_EXPORT_INTERVAL,OTEL_BSP_SCHEDULE_DELAY,OTEL_BLRP_SCHEDULE_DELAY,OTEL_SEMCONV_STABILITY_OPT_IN,OTEL_RESOURCE_ATTRIBUTES,OTEL_INSTRUMENTATION_A2A_SDK_ENABLED,OTEL_PYTHON_FASTAPI_EXCLUDED_URLS

# --no-cpu-throttling: Cloud Run otherwise pauses the CPU after each response, so spans wait
# in the exporter until the next request.
update() { gcloud run services update "$1" --project $P --region $R --quiet --no-cpu-throttling "${@:2}" >/dev/null && echo "updated $1"; }

if [[ "${1:-}" == "--off" ]]; then
  for s in "${BACKENDS[@]}"; do update $s --remove-env-vars "$OTEL_VARS" & done
  update diet-planner --remove-env-vars "$OTEL_VARS" --update-env-vars UI_CHECK_TELEMETRY_COLLECTOR=off,UI_LINKS=off &
  wait; exit 0
fi

URL=${1:?usage: connect_telemetry.sh https://<random>.trycloudflare.com}
URL=${URL%/}
TOKEN=$(grep '^TELEMETRY_TOKEN=' observability/stack/.env | cut -d= -f2 | tr -d '\r')
[[ -n "$TOKEN" ]] || { echo "TELEMETRY_TOKEN missing in observability/stack/.env"; exit 1; }

# The endpoint must accept the token before the services are pointed at it.
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$URL/v1/traces" -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" -d '{"resourceSpans":[]}')
[[ "$code" == "200" ]] || { echo "$URL/v1/traces answered $code, expected 200: is the tunnel up?"; exit 1; }

# Same settings as otel.env, plus the endpoint and its token. "|" separates the variables.
ENV="^|^OTEL_EXPORTER_OTLP_ENDPOINT=$URL|OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20$TOKEN"
ENV+="|OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf|OTEL_METRIC_EXPORT_INTERVAL=10000"
ENV+="|OTEL_BSP_SCHEDULE_DELAY=1000|OTEL_BLRP_SCHEDULE_DELAY=1000|OTEL_SEMCONV_STABILITY_OPT_IN=http"
ENV+="|OTEL_RESOURCE_ATTRIBUTES=service.namespace=diet-planner|OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false"
ENV+="|OTEL_PYTHON_FASTAPI_EXCLUDED_URLS=dev-ui,list-apps,favicon,builder,debug,apps/.*/users/.*/sessions$"

for s in "${BACKENDS[@]}"; do update $s --update-env-vars "$ENV" & done
# Web app: its telemetry dot checks the endpoint; its Grafana / Jaeger links open the local stack.
HOST=${URL#https://}
update diet-planner --update-env-vars "$ENV|OTEL_SERVICE_NAME=diet-web-ui|UI_CHECK_TELEMETRY_COLLECTOR=$HOST:443" \
  --remove-env-vars UI_LINKS &
wait
curl -s https://diet-planner-34656130941.us-central1.run.app/api/health; echo
