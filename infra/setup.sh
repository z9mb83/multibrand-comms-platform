#!/usr/bin/env bash
# One-time GCP setup for the Comms platform. Review before running; it creates billable resources.
# Usage: PROJECT_ID=my-proj REGION=asia-south1 GITHUB_REPO=owner/repo ./infra/setup.sh
set -euo pipefail
: "${PROJECT_ID:?}" "${GITHUB_REPO:?}"
REGION="${REGION:-asia-south1}"
SQL_INSTANCE="${SQL_INSTANCE:-comms-pg}"
gcloud config set project "$PROJECT_ID"

gcloud services enable run.googleapis.com sqladmin.googleapis.com cloudtasks.googleapis.com \
  cloudscheduler.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com \
  iamcredentials.googleapis.com gmail.googleapis.com

# Artifact Registry
gcloud artifacts repositories create comms --repository-format=docker --location="$REGION" || true

# Cloud SQL (Postgres 16)
gcloud sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_16 --region="$REGION" \
  --tier=db-custom-1-3840 --storage-auto-increase || true
gcloud sql databases create comms --instance="$SQL_INSTANCE" || true
DB_PASS="$(openssl rand -base64 24)"
gcloud sql users create comms --instance="$SQL_INSTANCE" --password="$DB_PASS" || true

# Service accounts
gcloud iam service-accounts create comms-runtime --display-name="Comms runtime" || true
gcloud iam service-accounts create comms-deploy --display-name="Comms GitHub deploy" || true
RUNTIME_SA="comms-runtime@$PROJECT_ID.iam.gserviceaccount.com"
DEPLOY_SA="comms-deploy@$PROJECT_ID.iam.gserviceaccount.com"
for role in roles/cloudsql.client roles/secretmanager.secretAccessor roles/cloudtasks.enqueuer roles/iam.serviceAccountUser roles/run.invoker; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$RUNTIME_SA" --role="$role" --condition=None >/dev/null
done
for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$DEPLOY_SA" --role="$role" --condition=None >/dev/null
done

# Secrets (values generated here; provider credentials are added later per sender account)
CONN="$PROJECT_ID:$REGION:$SQL_INSTANCE"
printf '%s' "postgresql+psycopg://comms:${DB_PASS}@/comms?host=/cloudsql/${CONN}" | gcloud secrets create database-url --data-file=- || true
openssl rand -hex 32 | tr -d '\n' | gcloud secrets create jwt-secret --data-file=- || true
openssl rand -hex 24 | tr -d '\n' | gcloud secrets create internal-token --data-file=- || true
openssl rand -hex 16 | tr -d '\n' | gcloud secrets create whatsapp-verify-token --data-file=- || true

# Cloud Tasks queue with bounded retries (dispatch is idempotent)
gcloud tasks queues create outbound --location="$REGION" --max-attempts=5 --min-backoff=30s --max-dispatches-per-second=5 || true

# Workload Identity Federation for GitHub Actions (no JSON keys)
gcloud iam workload-identity-pools create github --location=global || true
gcloud iam workload-identity-pools providers create-oidc github --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${GITHUB_REPO}'" || true
POOL="$(gcloud iam workload-identity-pools describe github --location=global --format='value(name)')"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/${POOL}/attribute.repository/${GITHUB_REPO}"

echo "Set these GitHub repository variables (Settings → Secrets and variables → Actions → Variables):"
echo "  GCP_PROJECT_ID=$PROJECT_ID  GCP_REGION=$REGION  CLOUD_SQL_INSTANCE=$CONN"
echo "  GCP_WIF_PROVIDER=${POOL}/providers/github  GCP_DEPLOY_SA=$DEPLOY_SA  GCP_RUNTIME_SA=$RUNTIME_SA"
echo "  API_PUBLIC_URL / WEB_PUBLIC_URL after the first deploy, then run infra/scheduler.sh"
