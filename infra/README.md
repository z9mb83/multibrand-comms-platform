# Infrastructure (Google Cloud)

| Component | Service |
|---|---|
| API (FastAPI) | Cloud Run `comms-api` |
| Web (Next.js standalone) | Cloud Run `comms-web` |
| Database | Cloud SQL for PostgreSQL 16 |
| Background dispatch | Cloud Tasks queue `outbound` → `POST /internal/dispatch/{id}` |
| Periodic jobs | Cloud Scheduler → `/internal/reports/run`, `/internal/dispatch-sweep`, `/internal/follow-ups/sweep`, `/internal/tasks/maintenance` |
| Secrets | Secret Manager (`database-url`, `jwt-secret`, `internal-token`, `whatsapp-verify-token`, one secret per sender account) |
| CI/CD | GitHub Actions with Workload Identity Federation (no service-account keys) |

## Order of operations
1. `PROJECT_ID=… GITHUB_REPO=owner/repo ./infra/setup.sh` — review the script first; it creates billable resources.
2. Add the printed values as GitHub **repository variables**.
3. Run the **Deploy to Cloud Run** workflow manually. Note the two service URLs, set `API_PUBLIC_URL` / `WEB_PUBLIC_URL` variables, and run it again.
4. `PROJECT_ID=… API_URL=… ./infra/scheduler.sh`.
5. Create the first Super Admin with a one-off Cloud Run job running `python -m scripts.seed` (then change the password), or insert a user manually.

## Sender credentials
Store each account's credentials as its own secret and put the reference on the account:

- Gmail: secret JSON `{"client_id","client_secret","refresh_token"}` → `gsm:projects/P/secrets/gmail-<brand>-<n>/versions/latest`
- WhatsApp: system-user access token; `provider_config` = `{"phone_number_id","waba_id"}`
- SMS: vendor API key; `provider_config` = `{"endpoint","dlt_entity_id","header","field_map"}`

The API refuses to start in production with the local default JWT secret or internal token.
