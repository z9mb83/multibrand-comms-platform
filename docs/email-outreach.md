# Email Outreach (ported from the Google Sheet + Cloud Run automation)

The **Email Outreach** page runs the same logic as `unified_email_automation_cloudrun`, with Postgres in place of the Sheet and Firestore.

| Sheet / Cloud Run | Comms Hub |
|---|---|
| `<Brand> Email list` settings (A2:C10) | Email Outreach → Settings (emergency stop, default daily/hourly limit, minimum gap, CC, sending start/end) |
| `<Brand> Email list` mailbox rows (A13:M) | Email Outreach → Mailboxes (enabled, warm-up stage, limits, paused, last sent, next eligible, last result) |
| Lead tab (Person Name … Follow-up Status) | Email Outreach → Leads |
| Send Log tab | Email Outreach → Send log |
| Suppression tab | Email Outreach → Suppression |
| `brands/<brand>/config.json` facts, phone, website, signature | Settings → AI drafts and signature |
| `gmail_tokens_bundle.json` / token files | Mailboxes connected with Google on Brands & Mailboxes (refresh tokens stored encrypted) |
| `OPENAI_API_KEY`, `OPENAI_MODEL`, `CROSS_BRAND_DUPLICATE_DAYS`, `MAX_DRAFTS_PER_BRAND_PER_RUN` | `OPENAI_API_KEY` env var on comms-api; the rest under Email Outreach → Automation |
| Firestore `round_robin`, `run_lock` | `outreach_global.next_start_index`, Postgres advisory lock |
| Cloud Scheduler → `email-sender` job | `.github/workflows/outreach-scheduler.yml` every 15 min 09:30–18:30 IST → `POST /internal/outreach/tick` |
| Cloud Scheduler → report jobs 09:00 / 18:00 | same workflow → `POST /internal/outreach/report/{morning,evening}` |
| `automation_report_receipts` | `outreach_report_receipts` |

## One run (per brand, brands in rotating order)
1. Skip if the brand is disabled or **Emergency stop** is on (new brands start with it on).
2. Write AI drafts for leads with an email and a blank subject or body (not Sent/Unsubscribed/Bounced), up to the per-run limit. Invalid emails → Skipped. Errors → Failed with `OpenAI generation error: …`.
3. Take the first lead (oldest first) with Approval = Yes and status Generated/Approved/Scheduled. None → stop.
4. Brand gap: if the brand's last send was less than *minimum gap* minutes ago → stop.
5. Checks, in order: invalid email → Skipped · blank subject/body → Scheduled · suppressed → Unsubscribed · already emailed by this brand → Skipped · emailed by another brand within the cross-brand window → Skipped · outside sending hours → Scheduled (stays approved).
6. Mailbox: enabled, not paused, connected, under its daily and hourly limit (counted from the Send Log), past its own gap and next-eligible time; pick the lowest *sent today ÷ daily limit*, then the one that sent longest ago. None → Scheduled "No eligible mailbox".
7. Send (plain text, CC to the brand's CC address) and record: lead Sent / Sent At / Sent From / Message ID / Follow-up Pending, a Send Log row, and the mailbox's last sent and next eligible time.

At most **one email per brand per run**.

**One addition:** a `Sending` row is written to the Send Log before Gmail is called. If anything fails after Gmail accepted the email, that row blocks a second send to the lead. The row shows as "Uncertain" until someone checks the mailbox's Sent folder.

## Moving over from the Sheet
For each brand:
1. **Add the brand's mailboxes and connect them with Google.** Do this on Brands & Mailboxes.
2. **Download the brand's tabs from Google Sheets and import them.** Use File → Download → CSV, then Email Outreach → Leads → Import from Sheet:
   - **Send Log.** Import this first, so nobody already emailed is emailed again.
   - **Suppression.**
   - **The lead tab.**
3. **Fill in Settings.** Add the facts, phone, website and CC, and set the limits and hours. Copy the values from the brand's `config.json` and Email list tab.
4. **Keep the old automation stopped once Comms Hub is sending.** Turn its Emergency stop to Yes, or pause its Cloud Scheduler jobs. Otherwise both systems email the same leads.
5. **Turn the brand's Emergency stop off in Comms Hub.**

Real delivery also needs `PROVIDER_MODE=auto` (or `live`) on comms-api. Until then, sends are recorded but simulated.
