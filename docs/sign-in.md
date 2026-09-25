# Sign-in and brand mailboxes

## Owner (Super Admin) sign-up
On a new installation the sign-in page shows **Create the owner account**. The first person to finish becomes the
Super Admin who owns the brand group:
- **Email me a code**: enter your email, then the 6-digit code emailed to you (needs email delivery configured), or
- **Sign up with Google**.
Once an owner exists, this is closed: nobody else can create an account for themselves.

## Signing in
- **Continue with Google** (the Google account's verified email must match an existing account; links on first use)
- **Email me a code** (passwordless)
- **Password** (optional fallback link)
Optional extra step: anyone can turn on *Email sign-in codes* in Settings → My security; after a password or Google
sign-in they then also enter an emailed code.

## Brands and mailboxes
Brands & Mailboxes page (Super Admin):
1. **Add a brand** with its **master mailbox** (e.g. `info@propertyexpertsgroup.com`).
2. **Add sub mailboxes** (e.g. `linkwithproperty1@gmail.com`). Campaign email is sent through the master and the subs
   in round-robin rotation.
3. For each mailbox:
   - **Connect with Google** (Gmail or Google Workspace addresses): you sign in *as that mailbox* and allow
     "send email" and "read email". This is what lets the platform send from it; it becomes *Connected · sending*.
     The Google account must be exactly that address.
   - **Verify with code**: a code is emailed to the mailbox. This proves you own it but does **not** allow sending.
Only connected, active mailboxes are used for sending, and never beyond their daily limit.

Access tokens are stored encrypted (Fernet) in the database. Set `ENCRYPTION_KEY` on the API to a long random value
and keep it stable; if it changes, mailboxes must be reconnected.

## Google Cloud setup (one OAuth client for everything)
Google Cloud Console → APIs & Services:
1. **Enable** the Gmail API.
2. **OAuth consent screen** (Google Auth Platform): app name "Comms Hub", user type External, your support email.
   Scopes: `openid`, `email`, `profile`, `https://www.googleapis.com/auth/gmail.send`,
   `https://www.googleapis.com/auth/gmail.readonly`.
   - While the app is in **Testing**, only listed test users can connect and Google expires their access after
     7 days. Add every mailbox as a test user, or **Publish** the app: unverified apps show a "Google hasn't verified
     this app" warning (continue via *Advanced*) and are limited to 100 users until Google verifies the app.
3. **Credentials → Create credentials → OAuth client ID → Web application**
   - Authorized redirect URI: `https://multibrand-comms-platform.vercel.app/api/auth/google/callback`
4. On the **comms-api** Vercel project set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (and `PUBLIC_APP_URL`), then redeploy.

## Email delivery for codes
On **comms-api**: `MAILER=smtp` + `MAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`
(Gmail/Workspace: an *app password*), or `MAILER=resend` + `MAIL_FROM` + `RESEND_API_KEY`.

## Sending for real
`PROVIDER_MODE=mock` (current) simulates all sending. Set `PROVIDER_MODE=auto` to send through connected mailboxes;
demo/mock senders stay simulated either way.
