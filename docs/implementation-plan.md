# Implementation plan

Status legend: ✅ done in this repo and covered by tests · 🟡 code written, needs live-credential verification · ⬜ not started

## Phase 1 — Foundation (✅ MVP)
| Item | Status | Where |
|---|---|---|
| Monorepo, Docker, docker-compose, CI | ✅ | `/`, `.github/workflows/ci.yml` |
| PostgreSQL schema (23 required tables + templates, suppressions) and Alembic migration | ✅ | `backend/app/models`, `backend/alembic` |
| JWT auth, bcrypt passwords, Super Admin, brand memberships and roles | ✅ | `app/api/deps.py`, `routes/auth.py` |
| Brand management (CRUD, members, templates) | ✅ | `routes/brands.py` |
| Dashboard shell with brand switcher + All Brands view | ✅ | `frontend/` |

## Phase 2 — Import, contacts, routing (✅ MVP)
| Item | Status |
|---|---|
| AUTO round-robin with per-brand persistent cursor and row-level locking | ✅ |
| FIXED override (held for review, never substituted) / STICKY / thread continuity | ✅ |
| Provider-aware + configured limits, pause, connection state, campaign allow-lists | ✅ |
| Assignment audit log (`sender_assignments`) + routing overview (next eligible sender) | ✅ |
| Import wizard API: upload → sheets/columns → mapping → preview/validate → commit | ✅ |
| Brand per file or brand column, sender validation, consent validation, dedupe, re-import protection, error workbook | ✅ |
| Imports create **draft** campaigns only — never send | ✅ |

## Phase 3 — Channel integrations (🟡)
| Item | Status | Notes |
|---|---|---|
| Mock providers (email/WhatsApp/SMS) | ✅ | used by all tests and local dev |
| Gmail API adapter (OAuth refresh-token per mailbox, threading headers, List-Unsubscribe) | 🟡 | needs a Google Cloud OAuth client + consent screen, then per-mailbox connect flow |
| Gmail OAuth connect flow (UI + callback storing refresh token in Secret Manager) | ⬜ | next task |
| Gmail inbound polling (history API → `/webhooks/email/inbound`) | ⬜ | adapter helper exists; scheduler job pending |
| WhatsApp Cloud API adapter + webhook (signature check, messages, statuses) | 🟡 | needs Meta app, WABA, phone_number_id, system-user token |
| SMS DLT adapter (provider-neutral, field mapping) | 🟡 | choose vendor; configure entity id, headers, templates |
| Bounce handling | 🟡 | status webhook marks hard bounces → suppression; Gmail bounce parsing pending |

## Phase 4 — Inbox, follow-ups, tasks (✅ MVP)
| Item | Status |
|---|---|
| Inbound ingestion: brand by receiving account, contact/conversation/campaign match, idempotent | ✅ |
| Rule-based reply classification + human override (LLM classifier pluggable) | ✅ |
| Unsubscribe keyword handling → per-brand suppression + consent withdrawal | ✅ |
| Follow-up rules (task / reminder / approval_send), sweep, max attempts, stop conditions | ✅ |
| Immediate pause on reply, human resume | ✅ |
| Daily tasks: CRUD, statuses, overdue marking, carry-forward, completion requires a user or system | ✅ |

## Phase 5 — Reporting, notifications, deployment (✅ MVP / 🟡 deploy)
| Item | Status |
|---|---|
| Master settings (emails, mobile, channel, times, timezone, working days, brand prefs, recipients) | ✅ |
| Morning/evening report builder (overall + per brand), HTML email, mobile text, XLSX attachment | ✅ |
| Scheduler-safe `run_due` (unique per kind/date), delivery with backoff retries and status tracking | ✅ |
| Dedicated notification senders independent of marketing rotation; mobile opt-in required | ✅ |
| Analytics overview API + dashboard | ✅ |
| Cloud Run / Cloud SQL / Cloud Tasks / Scheduler / Secret Manager deploy workflow | 🟡 written, needs GCP project + Workload Identity setup (`infra/README.md`) |

## Next steps (in order)
1. Create GCP project resources (`infra/setup.sh`) and set GitHub secrets for `deploy.yml`.
2. Gmail OAuth connect flow for each of the 10 mailboxes per brand; verify send + reply polling with one real mailbox.
3. Meta WhatsApp: register one number per brand, create notification + marketing templates, point webhook at `/webhooks/whatsapp`.
4. Pick SMS vendor, register DLT headers/templates, fill `provider_config.field_map`.
5. Replace rule-based classifier with an LLM call behind the same interface (keep human override).
6. Add Pub/Sub push for Gmail instead of polling once volume grows.
