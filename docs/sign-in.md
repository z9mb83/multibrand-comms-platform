# Sign-in options

## Password (always available)
Email + password. The first account is created on the first-run setup screen.

## Sign in with Google (optional)
Works only for people who already have a Comms Hub account. The Google account's **verified** email must match the
Comms Hub email; it links automatically on first use (Settings → My security shows the link and can unlink it).
Google sign-in never creates new accounts.

Setup (Google Cloud Console → APIs & Services):
1. OAuth consent screen: External (or Internal for Google Workspace), app name "Comms Hub", scopes `openid`, `email`, `profile`.
2. Credentials → Create credentials → OAuth client ID → **Web application**.
   - Authorized redirect URI: `https://<dashboard-domain>/api/auth/google/callback`
     (currently `https://multibrand-comms-platform.vercel.app/api/auth/google/callback`)
3. Set on the **API** deployment: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `PUBLIC_APP_URL=https://<dashboard-domain>`; redeploy.

Security: authorization-code flow; `state` is HMAC-signed, expires in 10 minutes and is bound to an HttpOnly cookie
(login-CSRF protection); the ID token's signature (Google JWKS), audience, issuer, expiry and nonce are verified.

## Email sign-in codes (optional, per user)
Each user can turn on "Email sign-in codes" in Settings → My security. After the password or Google step they must
enter a 6-digit code emailed to them. Turning it on or off itself requires a code, which proves the mailbox works.

- Codes: random 6 digits, stored only as an HMAC, 10-minute expiry, single use, 5 attempts.
- Limits: at most 5 codes per user per 15 minutes, 30 seconds between codes.
- Recovery: a Super Admin can turn codes off for another user (`POST /api/users/{id}/mfa/reset`).

Email delivery (required before anyone can turn codes on in production), on the **API** deployment:
- `MAILER=smtp` + `MAIL_FROM`, `SMTP_HOST`, `SMTP_PORT` (587 STARTTLS or 465 SSL), `SMTP_USERNAME`, `SMTP_PASSWORD`
  (for Gmail/Workspace use an **app password**, not the account password), or
- `MAILER=resend` + `MAIL_FROM` (verified domain) + `RESEND_API_KEY`.

This mailer is separate from the marketing sender accounts and their rotation.
