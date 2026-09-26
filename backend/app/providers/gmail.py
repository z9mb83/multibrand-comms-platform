"""Gmail API adapter (OAuth 2.0, one refresh token per mailbox).

The secret referenced by `sender_accounts.secret_ref` must contain JSON:
  {"client_id": "...", "client_secret": "...", "refresh_token": "..."}

STATUS: implemented against the documented Gmail REST API but NOT yet verified with
live credentials. Do not treat as working until tested with a real connected mailbox.
"""
import base64
import json
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import httpx

from app.core.secrets import resolve_secret
from app.providers.base import HealthResult, SendRequest, SendResult

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _access_token(secret_ref: str | None) -> str:
    creds = json.loads(resolve_secret(secret_ref))
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def build_mime(req: SendRequest) -> tuple[EmailMessage, str]:
    msg = EmailMessage()
    msg["From"] = formataddr((req.from_name or "", req.from_address))
    msg["To"] = req.to_address
    if req.cc:
        msg["Cc"] = req.cc
    msg["Subject"] = req.subject or ""
    message_id = make_msgid(domain=req.from_address.split("@")[-1])
    msg["Message-ID"] = message_id
    if req.in_reply_to:
        msg["In-Reply-To"] = req.in_reply_to
        msg["References"] = req.references or req.in_reply_to
    if req.list_unsubscribe:
        msg["List-Unsubscribe"] = req.list_unsubscribe
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["X-Idempotency-Key"] = req.idempotency_key
    msg.set_content(req.body or "")
    if req.body and "<" in req.body and not req.plain_text:
        msg.add_alternative(req.body, subtype="html")
    for filename, data, mime in req.attachments:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg, message_id


class GmailProvider:
    name = "gmail"

    def send(self, req: SendRequest) -> SendResult:
        try:
            token = _access_token(req.secret_ref)
            mime, message_id = build_mime(req)
            payload: dict = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()}
            if req.thread_id:
                payload["threadId"] = req.thread_id
            resp = httpx.post(f"{API}/messages/send", json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        except Exception as exc:  # network / secret errors
            return SendResult(ok=False, error=str(exc), retryable=True)
        if resp.status_code >= 400:
            retryable = resp.status_code in (429, 500, 502, 503, 504)
            return SendResult(ok=False, error=f"gmail {resp.status_code}: {resp.text[:300]}", retryable=retryable)
        data = resp.json()
        return SendResult(ok=True, provider_message_id=data.get("id"), thread_id=data.get("threadId"), message_ref=message_id)

    def check_health(self, address: str, sender_config: dict, secret_ref: str | None) -> HealthResult:
        try:
            token = _access_token(secret_ref)
            resp = httpx.get(f"{API}/profile", headers={"Authorization": f"Bearer {token}"}, timeout=20)
            if resp.status_code != 200:
                return HealthResult(False, f"profile {resp.status_code}")
            if resp.json().get("emailAddress", "").lower() != address.lower():
                return HealthResult(False, "token belongs to a different mailbox")
            return HealthResult(True, "ok")
        except Exception as exc:
            return HealthResult(False, str(exc))

    @staticmethod
    def profile_email(secret_ref: str | None) -> str:
        token = _access_token(secret_ref)
        resp = httpx.get(f"{API}/profile", headers={"Authorization": f"Bearer {token}"}, timeout=20)
        resp.raise_for_status()
        return str(resp.json().get("emailAddress", "")).lower()

    @staticmethod
    def thread_metadata(secret_ref: str | None, thread_id: str, headers: list[str], token: str | None = None) -> dict:
        """users.threads.get with format=metadata (reply check for the daily reports; bodies are not read)."""
        token = token or _access_token(secret_ref)
        resp = httpx.get(f"{API}/threads/{thread_id}", params=[("format", "metadata")] + [("metadataHeaders", h) for h in headers],
                         headers={"Authorization": f"Bearer {token}"}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def access_token(secret_ref: str | None) -> str:
        return _access_token(secret_ref)

    @staticmethod
    def list_history(secret_ref: str | None, start_history_id: str) -> dict:
        token = _access_token(secret_ref)
        resp = httpx.get(
            f"{API}/history",
            params={"startHistoryId": start_history_id, "historyTypes": "messageAdded"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
