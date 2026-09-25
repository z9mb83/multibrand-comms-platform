# Multi-Brand Communication Platform

One monorepo to run Email, WhatsApp and SMS outreach for several independent brands: intelligent
sender routing, Excel bulk import, a unified inbox, follow-up automation, daily tasks and twice-daily
master reports.

- **Architecture:** [`architecture.md`](architecture.md)
- **Database schema:** [`docs/database-schema.md`](docs/database-schema.md) (generated from the models)
- **Implementation plan & status:** [`docs/implementation-plan.md`](docs/implementation-plan.md)
- **Live-provider checklist:** [`docs/provider-verification.md`](docs/provider-verification.md)
- **GCP deployment:** [`infra/README.md`](infra/README.md)

> **Integration status.** All channels run against functional **mock providers** in development and
> tests. The Gmail, WhatsApp Cloud API and SMS adapters are implemented but have **not** been verified
> with live credentials — see the checklist before relying on them.

## Stack
Next.js 15 · React 19 · TypeScript · Tailwind · shadcn-style UI — FastAPI · SQLAlchemy 2 · Pydantic 2 ·
Alembic — PostgreSQL 16 — Cloud Run · Cloud SQL · Cloud Tasks · Cloud Scheduler · Secret Manager — GitHub Actions.

## Folder structure
```
.
├── architecture.md
├── docker-compose.yml            # local Postgres + API + web
├── .github/workflows/
│   ├── ci.yml                    # ruff, migrations check, pytest on Postgres; Next lint/typecheck/build
│   └── deploy.yml                # manual Cloud Run deploy via Workload Identity Federation
├── backend/
│   ├── alembic/versions/         # 0001_initial_schema.py
│   ├── app/
│   │   ├── api/deps.py           # auth, brand scope, roles, internal-endpoint auth
│   │   ├── api/routes/           # auth, brands, senders, contacts, imports, campaigns, inbox,
│   │   │                         # workflow (follow-ups + tasks), reports, webhooks, internal
│   │   ├── core/                 # config, db, security (JWT/bcrypt), secret refs
│   │   ├── models/               # 25 tables: tenancy, senders, contacts, imports, messaging, workflow, reporting
│   │   ├── providers/            # base interface, mock, gmail, whatsapp_cloud, sms, registry
│   │   ├── schemas/api.py
│   │   └── services/             # routing, dispatch, imports, contacts/consent, inbound, followups,
│   │                             # tasks, campaigns, reports, queue
│   ├── scripts/                  # seed.py, gen_schema_doc.py
│   └── tests/                    # 51 tests on real Postgres
├── frontend/
│   └── src/
│       ├── app/(app)/            # Overview, Brands, Contacts, Excel Import, Compose, Campaigns,
│       │                         # accounts/[channel], Unified Inbox, Follow-ups, Daily Tasks,
│       │                         # Master Reports, Analytics, Settings
│       ├── app/login/
│       ├── components/           # shell (nav, brand switcher), app-context, ui/*
│       └── lib/                  # api client, types, utils
├── docs/
└── infra/                        # setup.sh, scheduler.sh, README.md
```

## Run locally
With Docker:
```bash
docker compose up --build
# web http://localhost:3000   api http://localhost:8000/docs
# login: admin@example.com / ChangeMe123!   (seeded, local only)
```
Without Docker (Postgres running locally):
```bash
cd backend
pip install -r requirements-dev.txt
cp ../.env.example .env                  # adjust DATABASE_URL
alembic upgrade head && python -m scripts.seed
uvicorn app.main:app --reload --port 8000

cd ../frontend
npm install && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

## Tests
```bash
cd backend
createdb comms_test                                   # tests use their own database
DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/comms_test pytest -q
```
Covers: round-robin routing, concurrent assignment (10 threads × 10), fixed overrides and hold-for-review,
sticky and thread continuity, Excel brand mapping, duplicate/re-import prevention, consent enforcement,
reply matching, follow-up pause/cancel on reply, unsubscribe, daily report generation, notification
retries, brand isolation and duplicate-send prevention.

## Safety rules built in
- Imports create drafts only; campaigns need approval before launch; emergency stop per campaign or globally.
- Routing never uses an account that is disconnected, paused, over its configured or provider limit.
- A FIXED sender that is unavailable holds the message for review — no silent substitution.
- Replies are assigned to a brand only by the receiving account, never by the contact's email/phone.
- Consent and suppression are per brand and per channel; STOP/UNSUBSCRIBE replies are honoured automatically.
- No credentials, contacts or production configuration in git (`.gitignore`, Secret Manager refs).
