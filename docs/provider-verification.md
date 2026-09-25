# Provider verification checklist

The Gmail, WhatsApp Cloud API and SMS adapters are written against the providers' documented
APIs and are unit-tested offline (payload construction, headers, signatures), but **none has been
tested with live credentials yet**. Treat each channel as unverified until every box below is ticked
with a real account.

## Gmail (per mailbox)
- [ ] OAuth client created (Google Cloud console), consent screen configured, scope `https://www.googleapis.com/auth/gmail.modify`
- [ ] Refresh token stored in Secret Manager; `secret_ref` set on the sender account
- [ ] `POST /api/brands/{id}/senders/{sid}/health-check` returns healthy and the profile email matches
- [ ] Send one message; confirm Message-ID, `List-Unsubscribe` and threading in the recipient's client
- [ ] Reply from the recipient; confirm it lands in the Unified Inbox under the right brand and conversation
- [ ] Record the Workspace/Gmail daily sending limit as `provider_daily_limit`

## WhatsApp Cloud API (per brand number)
- [ ] Number registered in WhatsApp Manager; `phone_number_id` + `waba_id` in `provider_config`
- [ ] Permanent system-user token stored in Secret Manager
- [ ] Webhook URL `https://<api>/webhooks/whatsapp` verified with `WHATSAPP_VERIFY_TOKEN`; app secret set for signature checks
- [ ] Template approved; send a template message; delivery receipt updates status to delivered/read
- [ ] Inbound reply appears in the inbox; "STOP" suppresses the number for that brand only
- [ ] Record the messaging tier limit as `provider_daily_limit`

## SMS (per brand header)
- [ ] DLT entity, header and templates registered with the operator
- [ ] Vendor endpoint, auth header and `field_map` configured; API key in Secret Manager
- [ ] Send using an approved DLT template id; delivery callback posted to `/webhooks/status`
- [ ] Inbound (if the vendor supports it) posted to `/webhooks/sms/inbound`

## Master notifications
- [ ] Dedicated notification sender(s) created with "notification sender" flag and selected in Settings
- [ ] Master mobile opt-in recorded; WhatsApp report template approved
- [ ] Morning report received at the configured time on a working day
