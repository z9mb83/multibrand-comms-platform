#!/usr/bin/env bash
# Cloud Scheduler jobs calling the API's /internal endpoints with OIDC tokens.
# Usage: PROJECT_ID=.. REGION=asia-south1 API_URL=https://comms-api-xxxx.run.app ./infra/scheduler.sh
set -euo pipefail
: "${PROJECT_ID:?}" "${API_URL:?}"
REGION="${REGION:-asia-south1}"
SA="comms-runtime@$PROJECT_ID.iam.gserviceaccount.com"
job() {  # name schedule path
  gcloud scheduler jobs create http "$1" --location="$REGION" --schedule="$2" --time-zone="Asia/Kolkata" \
    --uri="$API_URL$3" --http-method=POST --oidc-service-account-email="$SA" --oidc-token-audience="$API_URL" \
    || gcloud scheduler jobs update http "$1" --location="$REGION" --schedule="$2" --time-zone="Asia/Kolkata" \
    --uri="$API_URL$3" --http-method=POST --oidc-service-account-email="$SA" --oidc-token-audience="$API_URL"
}
job comms-reports        "*/15 * * * *" /internal/reports/run          # generates when due; idempotent
job comms-dispatch-sweep "*/2 * * * *"  /internal/dispatch-sweep       # picks up scheduled/retry messages
job comms-followups      "*/10 * * * *" /internal/follow-ups/sweep
job comms-task-maint     "5 0 * * *"    /internal/tasks/maintenance    # carry-forward + overdue, 00:05 IST
