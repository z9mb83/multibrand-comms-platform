# Architecture — Multi-Brand Communication Platform

## 1. Goals

One monorepo that lets a small operations team run Email, WhatsApp and SMS
outreach for **several independent brands** (5 initially), with:

- strict per-brand isolation of contacts, credentials, senders, templates, campaigns and reports;
- deterministic, auditable sender routing (AUTO round-robin / FIXED / STICKY);
- safe bulk import from Excel that **never sends without explicit approval**;
- a unified inbox, follow-up engine and daily task board;
- twice-daily master reports to the Super Admin by email and WhatsApp/SMS.

Non-goals: rotating accounts to evade provider limits, anti-spam rules or
restrictions. The router only chooses among accounts that are healthy **and**
within their provider limits; when none is eligible, messages wait.

## 2. High-level view

```
                        ┌──────────────────────────────┐
  Browser (desktop/     │  frontend/  Next.js 14 (App  │
  mobile) ────────────► │  Router) + Tailwind + shadcn │
                        └──────────────┬───────────────┘
                                       │ HTTPS / JSON (JWT)
                        ┌──────────────▼───────────────┐
                        │ backend/  FastAPI  (Cloud Run │
                        │ service "api")               │
                        │  routers → services → models │
                        └───┬──────────┬───────────┬───┘
                            │          │           │
              SQLAlchemy 2  │          │ enqueue   │ provider adapters
                            ▼          ▼           ▼
                    ┌──────────┐ ┌───────────┐ ┌────────────────────────┐
                    │ Cloud SQL│ │Cloud Tasks│ │ Gmail API  (OAuth2)    │
                    │ Postgres │ │ queues    │ │ Meta WhatsApp Cloud API│
                    └──────────┘ └─────┬─────┘ │ SMS (DLT) provider     │
                                       │       │ Mock providers (dev)   │
                  Cloud Scheduler ─────┤       └────────────────────────┘
                  (cron: reports,      ▼
                   follow-up sweep,  /internal/* endpoints on the same
                   inbox polling)     API service (OIDC-authenticated)

  Secret Manager: OAuth refresh tokens, WhatsApp tokens, SMS API keys,
                  JWT signing key, DB password.  DB stores only secret *refs*.
```

### Why one API service with internal endpoints
Cloud Tasks and Cloud Scheduler call HTTP targets. Exposing `/internal/*`
routes on the same FastAPI image (protected by an OIDC audience check in
production and a shared token locally) keeps deployment to one image while
still getting controlled, retryable background processing. A second Cloud Run
service (`worker`) can run the same image with only internal routes enabled
when volume justifies it.

## 3. Backend layering

| Layer | Folder | Responsibility |
|-------|--------|----------------|
| API | `app/api/` | HTTP routers, auth dependencies, brand-scope enforcement |
| Schemas | `app/schemas/` | Pydantic request/response models |
| Services | `app/services/` | Business logic: routing, import, dispatch, inbound, follow-ups, tasks, reports, notifications, audit |
| Providers | `app/providers/` | Channel adapters behind one interface (`EmailProvider`, `WhatsAppProvider`, `SmsProvider`), plus mocks |
| Models | `app/models/` | SQLAlchemy ORM, one module per domain |
| Infra | `app/core/` | config, DB session, security (JWT/bcrypt), secret resolution, background queue abstraction |

Rules:
- Routers never touch providers directly; they call services.
- Every service function that touches brand data takes `brand_id` explicitly
  and every query filters on it. Cross-brand references are validated
  (`ensure_same_brand`) before writes.
- Side effects (sending) happen only in `dispatch.process_outbound(message_id)`,
  which is idempotent.

## 4. Multi-brand isolation and access

- `brands` is the tenant root. Brand-owned tables carry a non-null `brand_id`.
- `brand_memberships(user_id, brand_id, role)` grants access; roles:
  `brand_admin`, `manager`, `agent`, `viewer`.
- `users.is_super_admin` sees the **All Brands** view, but only for brands it is
  authorised for (super admins are authorised for all by default; this can be
  restricted by membership rows with `role='blocked'`).
- Every brand-scoped route uses the `BrandScope` dependency, which resolves the
  `X-Brand-Id` header / path param and raises 403 unless the user is a member.
- Contacts: a global `contacts` row (person identity) + `brand_contacts`
  (brand-specific record: owner, tags, status, sticky sender). Consent and
  suppression are **per brand** (`contact_consents`, `brand_contacts.unsubscribed_*`).
  Inbound messages are matched to a brand by the **receiving sender account**,
  never by the contact's email/phone alone.

## 5. Sender routing (Module 1)

`services/routing.py::assign_sender(db, brand_id, channel, contact, mode, fixed_sender, conversation)`

1. **Thread continuity** — if the message belongs to an existing conversation
   with a sender, that sender is used (mode is recorded as `STICKY`).
2. **FIXED** — the named sender must belong to the brand and be eligible;
   otherwise the message is set to `held_for_review` with the reason. No substitution.
3. **STICKY** — use `brand_contacts.sticky_sender_{channel}_id`; if none yet, fall
   through to AUTO and then persist the choice as the sticky sender.
4. **AUTO** — `SELECT … FROM sender_routing_state WHERE brand_id=? AND channel=? FOR UPDATE`
   locks the per-brand cursor row. Candidates are the brand's accounts ordered by
   `(rotation_order, id)`. Starting after `last_sender_id`, the first account that passes
   `is_eligible()` is chosen, the cursor advances and the usage counter is incremented
   in the same transaction. Concurrent workers serialise on that row lock, so no two
   messages can observe the same cursor value.

`is_eligible(account)` = `status == 'active'` and `connection_status == 'connected'`
and not paused and `sent_today < min(daily_limit, provider_daily_limit)` and
`sent_this_hour < hourly_limit` and campaign allow-list (if any) contains it.
Counters reset by date/hour bucket (`usage_date`, `usage_hour`) lazily.

If nothing is eligible the message stays `queued` with `hold_reason='no_eligible_sender'`
and is retried later — it is **not** sent through an over-limit account.

Every attempt (chosen or rejected) is written to `sender_assignments`.

## 6. Excel import (Module 2)

Three-step API mirroring the wizard:

1. `POST /imports/upload` → stores the file (GCS in prod, local dir in dev),
   computes SHA-256, lists sheets + detected columns (synonym matching, configurable).
   Same hash already committed for the same brand ⇒ flagged `duplicate_file`.
2. `POST /imports/{id}/preview` with mapping + options (sheet, brand mode
   `single|column`, default routing mode, schedule) → validates each row into
   `import_rows` with status `valid|invalid|duplicate|skipped` and error text.
   Validation: email/phone format (E.164, default +91), brand resolvable and
   user-authorised, `sender_email` belongs to that brand, consent flag for the
   channel, dedupe within file (email/phone per brand) and against existing
   brand contacts, per-row idempotency key `sha256(batch|brand|channel|recipient|campaign)`.
3. `POST /imports/{id}/commit` → upserts contacts/brand_contacts/consents and, if
   message columns exist, creates **draft** outbound messages attached to a
   **draft campaign** needing approval. Nothing is dispatched.
   `GET /imports/{id}/errors.xlsx` returns the error workbook.

## 7. Dispatch pipeline and reliability

```
campaign approved ─► outbound_messages (status=queued, idempotency_key UNIQUE)
                        │  enqueue task (Cloud Tasks / inline in dev)
                        ▼
   process_outbound(id): SELECT … FOR UPDATE SKIP LOCKED
      ├ already sent/sending?  → no-op   (duplicate-send prevention)
      ├ consent + suppression + campaign state checks
      ├ assign sender (routing)             ─► held_for_review / waiting
      ├ status=sending, commit
      ├ provider.send(idempotency_key)      ─► message_attempts row
      └ status=sent|failed, provider_message_id
```

- `outbound_messages.idempotency_key` is unique ⇒ re-imports and retried API
  calls cannot create duplicates.
- A message in `sending` longer than the lease (5 min) is reconciled, not blindly resent:
  providers are asked by idempotency key where supported; otherwise it is marked `unknown`
  for human review.
- Campaign `paused` / `emergency_stopped` is checked immediately before every send.

## 8. Inbound + inbox (Module 4)

Sources: Gmail API history polling per connected account (scheduler every 2 min, or
Pub/Sub push), WhatsApp Cloud API webhook, SMS provider callback.
`services/inbound.py::ingest()` performs: identify brand via receiving sender account →
match contact by address within that brand → find conversation by thread id / external id /
(brand, contact, channel) → link campaign → save `inbound_messages` → update conversation
(`status='needs_reply'`, `last_inbound_at`) → **pause pending follow-ups** → detect
unsubscribe keywords (STOP, UNSUBSCRIBE) → notify owner. Optional AI classification
stored in `ai_classification` with `classification_source='ai'|'human'`; humans can override.

## 9. Follow-ups and tasks (Modules 5 & 6)

`follow_up_rules` (per campaign or brand default): `steps` JSON e.g.
`[{"after_days":2,"mode":"task"},{"after_days":6,"mode":"approval_send","template_id":…}]`,
`max_attempts`, `stop_on_reply`. A scheduler sweep creates `follow_up_tasks` whose
`due_at` has passed and are still eligible. Stop conditions: reply (if configured),
unsubscribe, consent withdrawn, max attempts, conversation closed, human pause.
On reply every pending task for that conversation is set `paused_on_reply` until reviewed.

`daily_tasks` hold manual and system tasks. Overdue is computed (`due_at < now`
and status in pending/in_progress) and materialised by a sweep; the carry-forward
rule moves incomplete tasks to the next working day and records `carried_from`.
Completed status requires `completed_by` (user) or `completed_source='system'`.

## 10. Master reporting (Module 3)

`master_report_settings` is a singleton row: master email, master mobile, channel
(`email|whatsapp|sms|email+whatsapp`), morning/evening times, timezone (Asia/Kolkata),
working days, per-brand preferences, recipients, emergency alerts.
Cloud Scheduler hits `/internal/reports/run?kind=morning|evening` every 15 minutes;
the service checks whether a report is due for the local time and whether one already
exists for `(kind, report_date)` (unique), which makes the trigger idempotent.
Reports are built from DB facts only (sent = provider-accepted, completed = recorded
by system or confirmed by user). Delivery is recorded in `notification_deliveries`
with retry/backoff; it uses a dedicated notification sender, **independent of
marketing rotation**.

## 11. Providers (Module 7)

| Channel | Production adapter | Status in this repo |
|---------|--------------------|---------------------|
| Email | Gmail API with OAuth 2.0 per account (refresh token in Secret Manager), `threadId` + `In-Reply-To`/`References` headers, `List-Unsubscribe` header | Adapter written, **not verified against live credentials** |
| WhatsApp | Meta WhatsApp Cloud API: template + session messages, webhook signature verification (X-Hub-Signature-256), delivery receipts | Adapter written, **not verified against live credentials** |
| SMS | Provider-neutral HTTP adapter with DLT fields (`entity_id`, `header`, `template_id`) + status callbacks | Adapter written, **not verified against live credentials** |
| All | `Mock*Provider` — deterministic, records calls, can simulate failures | Used by all tests |

## 12. Security

- JWT (HS256 locally, key from Secret Manager in prod), bcrypt password hashes.
- Credentials never stored in DB: `sender_accounts.secret_ref` points to a Secret Manager
  version (`projects/…/secrets/…/versions/latest`). Local dev uses `env:` refs.
- Webhook signature validation, `/internal/*` protected by OIDC (prod) / token (dev).
- `audit_logs` for every privileged change and every send decision.
- `.gitignore` excludes `.env*`, uploads, exports; no contacts or production config in git.

## 13. Deployment

- Docker images: `backend/Dockerfile`, `frontend/Dockerfile`.
- GitHub Actions: `ci.yml` (ruff + pytest on Postgres service, Next.js lint/build) on every
  push/PR; `deploy.yml` (manual/`main`) builds, pushes to Artifact Registry, runs Alembic
  migrations via a Cloud Run job, deploys `api` and `web` services using Workload Identity
  Federation (no JSON keys in GitHub).
- Cloud SQL (Postgres 16) via the Cloud SQL connector / unix socket.
