"""Transactional email (sign-in codes). Independent of the marketing sender accounts and their rotation.

MAILER=log     development only: nothing is sent, the message is kept in memory/logged
MAILER=smtp    any SMTP server (e.g. Google Workspace / Gmail with an app password, SES, Zoho)
MAILER=resend  Resend HTTP API (RESEND_API_KEY)
Otherwise, if the Super Admin picked a Google-connected mailbox under Settings ("Send sign-in codes from"),
codes are sent from that Gmail mailbox through the Gmail API. No SMTP password is needed.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)
DEV_OUTBOX: list[dict] = []  # MAILER=log keeps messages here (tests / local dev)


class MailerError(RuntimeError):
    pass


def code_sender(db):
    """The connected Gmail mailbox chosen to send codes, or None."""
    if db is None:
        return None
    from app.models import AppSettings, SenderAccount
    row = db.get(AppSettings, 1)
    acc = db.get(SenderAccount, row.code_sender_id) if row and row.code_sender_id else None
    if acc and acc.provider == "gmail" and acc.connection_status == "connected" and acc.secret_ref:
        return acc
    return None


def can_deliver(db=None) -> bool:
    """True when real email delivery is configured (or we're not in production)."""
    s = get_settings()
    if s.mailer == "smtp":
        return bool(s.smtp_host and s.mail_from)
    if s.mailer == "resend":
        return bool(s.resend_api_key and s.mail_from)
    return code_sender(db) is not None or s.app_env != "production"


def _send_via_gmail(acc, to: str, subject: str, text: str) -> None:
    from app.providers.base import SendRequest
    from app.providers.gmail import GmailProvider
    res = GmailProvider().send(SendRequest(channel="email", from_address=acc.address, from_name="Comms Hub", to_address=to,
                                           subject=subject, body=text, plain_text=True, secret_ref=acc.secret_ref))
    if not res.ok:
        raise MailerError(f"Gmail delivery from {acc.address} failed")


def send_email(to: str, subject: str, text: str, html: str | None = None, db=None) -> None:
    s = get_settings()
    if s.mailer == "log":
        acc = code_sender(db)
        if acc is not None:
            _send_via_gmail(acc, to, subject, text)
            return
        if s.app_env == "production":
            raise MailerError("Email isn't set up yet. The Super Admin can pick a mailbox under Settings → Sign-in codes.")
        DEV_OUTBOX.append({"to": to, "subject": subject, "text": text})
        log.warning("MAILER=log: email to %s not sent (subject=%r)", to, subject)
        return
    if s.mailer == "smtp":
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = s.mail_from, to, subject
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        try:
            if s.smtp_port == 465:
                with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ssl.create_default_context(), timeout=20) as smtp:
                    if s.smtp_username:
                        smtp.login(s.smtp_username, s.smtp_password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
                    smtp.starttls(context=ssl.create_default_context())
                    if s.smtp_username:
                        smtp.login(s.smtp_username, s.smtp_password)
                    smtp.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            raise MailerError(f"SMTP delivery failed: {exc.__class__.__name__}") from exc
        return
    if s.mailer == "resend":
        try:
            r = httpx.post("https://api.resend.com/emails", timeout=20,
                           headers={"Authorization": f"Bearer {s.resend_api_key}"},
                           json={"from": s.mail_from, "to": [to], "subject": subject, "text": text,
                                 **({"html": html} if html else {})})
        except httpx.HTTPError as exc:
            raise MailerError("Email API unreachable") from exc
        if r.status_code >= 400:
            raise MailerError(f"Email API error {r.status_code}")
        return
    raise MailerError(f"Unknown MAILER '{s.mailer}'")
