"""Provider webhooks. WhatsApp Cloud API payloads are parsed per Meta's documented format;
signature verification is enforced when an app secret is configured."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import internal_auth
from app.core.config import get_settings
from app.core.db import get_db
from app.models import SenderAccount
from app.services.common import DomainError
from app.services.inbound import InboundEvent, apply_delivery_status, ingest

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/whatsapp", response_class=PlainTextResponse)
def whatsapp_verify(mode: str = Query(alias="hub.mode"), token: str = Query(alias="hub.verify_token"),
                    challenge: str = Query(alias="hub.challenge")):
    if mode == "subscribe" and token == get_settings().whatsapp_verify_token:
        return challenge
    raise HTTPException(403, "verification failed")


@router.post("/whatsapp")
async def whatsapp_receive(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    s = get_settings()
    if s.whatsapp_app_secret_ref:
        from app.core.secrets import resolve_secret
        from app.providers.whatsapp_cloud import verify_signature
        if not verify_signature(resolve_secret(s.whatsapp_app_secret_ref), body,
                                request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(401, "bad signature")
    elif s.app_env == "production":
        raise HTTPException(500, "WhatsApp app secret not configured")
    payload = await request.json()
    received = statuses = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            acc = None
            if phone_number_id:
                for a in db.scalars(select(SenderAccount).where(SenderAccount.channel == "whatsapp")):
                    if (a.provider_config or {}).get("phone_number_id") == phone_number_id:
                        acc = a
                        break
            for m in value.get("messages", []):
                if acc is None:
                    continue
                text = (m.get("text") or {}).get("body") or (m.get("button") or {}).get("text") or f"[{m.get('type')}]"
                ingest(db, InboundEvent(channel="whatsapp", to_address=None, receiving_account_id=acc.id,
                                        from_address="+" + m["from"].lstrip("+"), provider_message_id=m["id"],
                                        body=text, received_at=datetime.fromtimestamp(int(m.get("timestamp", 0)) or
                                                                                     datetime.now().timestamp(), timezone.utc),
                                        raw=m))
                received += 1
            for st in value.get("statuses", []):
                if apply_delivery_status(db, "whatsapp", st.get("id"), st.get("status")):
                    statuses += 1
    db.commit()
    return {"received": received, "statuses": statuses}


class SmsInbound(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    to: str  # DLT header / virtual number that received it
    from_: str | None = Field(default=None, alias="from")
    sender: str | None = None
    text: str = ""
    message_id: str


@router.post("/sms/inbound", dependencies=[Depends(internal_auth)])
def sms_inbound(body: SmsInbound, db: Session = Depends(get_db)):
    try:
        msg = ingest(db, InboundEvent(channel="sms", to_address=body.to, from_address=body.from_ or body.sender or "",
                                      provider_message_id=body.message_id, body=body.text))
    except DomainError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    db.commit()
    return {"id": msg.id}


class StatusIn(BaseModel):
    channel: str
    provider_message_id: str
    status: str


@router.post("/status", dependencies=[Depends(internal_auth)])
def delivery_status(body: StatusIn, db: Session = Depends(get_db)):
    ok = apply_delivery_status(db, body.channel, body.provider_message_id, body.status)
    db.commit()
    return {"matched": ok}


class EmailInbound(BaseModel):
    """Normalised email reply, produced by the Gmail poller (or posted manually in dev)."""
    mailbox: str
    from_address: str
    subject: str | None = None
    body: str | None = None
    message_id: str
    thread_id: str | None = None
    in_reply_to: str | None = None


@router.post("/email/inbound", dependencies=[Depends(internal_auth)])
def email_inbound(body: EmailInbound, db: Session = Depends(get_db)):
    try:
        msg = ingest(db, InboundEvent(channel="email", to_address=body.mailbox, from_address=body.from_address,
                                      provider_message_id=body.message_id, subject=body.subject, body=body.body,
                                      thread_id=body.thread_id, in_reply_to=body.in_reply_to))
    except DomainError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    db.commit()
    return {"id": msg.id, "brand_id": msg.brand_id, "conversation_id": msg.conversation_id}
