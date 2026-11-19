# CLAUDE.md - multibrand-comms-platform

Read this first, then `README.md`, `architecture.md` and `docs/implementation-plan.md`.

## Status: not in use
This was the first, large plan (Sep 2026) for the owner's Email / WhatsApp / SMS outreach: FastAPI + SQLAlchemy + Alembic,
PostgreSQL, Next.js 15 dashboard, Cloud Run / Cloud SQL / Cloud Tasks / Secret Manager, GitHub Actions. Channels run on mock
providers; the Gmail, WhatsApp Cloud API and SMS adapters were never verified with live credentials.

The owner then chose a **simple, free, sheet-based tool** instead: the Google Sheet "Automation Control Center"
(repo `unified-communication-automation`) with a light Vercel dashboard (repo `automation-dashboard`). Reasons: he does not pay
for anything (Cloud SQL, paid Actions), and he wants minimal code that his IT staff can repair. Treat this repo as a reference /
archive; do not deploy it or spend money on it unless the owner explicitly asks.

## Useful parts if it is ever revived
- Sender routing (round-robin with per-brand cursor, fixed/sticky senders, hold-for-review), Excel import wizard, consent and
  suppression per brand and channel, unified inbox, follow-ups, daily tasks, twice-daily master reports.
- Local run: `docker compose up --build` (web :3000, api :8000). Tests: `cd backend && pytest -q` on a local Postgres.
- Hard-coded references to the old GitHub owner: `backend/app/core/config.py` (`scheduler_github_repo` =
  `linkwithmarketing-design/multibrand-comms-platform`) and the tests - change them to `z9mb83/...` if it is ever deployed again.

19 Nov 2026: repo copied to GitHub z9mb83 with full history.

## Owner and how to work with him

- Owner: the 99 group of brands (99 Consulting / 99 Marketing Services). Brand codes used everywhere:
  **SB** Shaadi Bells · **99M** 99 Marketing Services · **99S** 99 Staffing Services · **PE** Property Experts Group · **SC** Skyline Capital (business buying/selling).
- The sheets, Apps Script projects and Gmail senders belong to the owner's main Google account (the 99 Marketing "linkwithmarketing" account).
- GitHub: this repo was copied with its full history from `linkwithmarketing-design/<repo>` to `z9mb83/<repo>` on 19 Nov 2026
  (a fresh copy, not a GitHub transfer). The old copy still exists; new work goes to **z9mb83**.
- The owner is not a programmer. Explain in plain words and give the result, not a list of steps you took.
- He wants a simple internal tool, not a product: only what he asks for, as little code and as few files as possible, no Docker or
  other maintenance-heavy setups. Keep old code (move it to `archive/`) instead of deleting it.
- **He does not pay for anything.** Free tiers only (Apps Script, Vercel Hobby, free Cloud Run/Cloud Shell use). No paid GitHub Actions/Codespaces.
- Update `README.md` (and `TROUBLESHOOTING.md` where there is one) with every change, in plain language.

### Hard rules (from the owner, always in force)
- Never commit or print secrets. Keys live in Apps Script **Script Properties**, **Vercel environment variables** or **Secret Manager** - never in code, git or normal sheet cells.
- Never put raw Meta access tokens, Google OAuth refresh tokens or SMS API secrets in spreadsheet cells.
- The owner types API keys, passwords and OAuth / device codes himself. Claude never does.
- No live emails, WhatsApp or SMS to real customers, no production campaign switched on, and no redeploy of the running email service or its scheduler without a separate, explicit go-live instruction.
- Never overwrite existing leads, statuses, approval values or formulas in the sheets. Never delete sheet tabs the automation reads.
- Never force-push or overwrite unrelated work. Work on a branch and open a pull request; **merge only when the owner says "merge"**.
- Do not rotate or destroy production credentials without his authorization.

### How changes usually reach production
Most logic runs in **Google Apps Script**, which is not connected to git. The usual loop (done through Claude in Chrome, signed in to the owner's main Google account):
1. Make the change in the repo file and test it offline (each repo has a `test/` or `tests/` folder).
2. Apply the same change in the Apps Script editor (exact text replacement in the editor, then the **Save project to Drive** button; reload to check it saved).
3. If the script is a web app: **Deploy → Manage deployments → Edit (pencil) → Version: New version → description → Deploy**. Editing the existing deployment keeps the `/exec` URL the same.
4. Commit, push the branch, open a PR, wait for "merge". The repo must always match what is live.
