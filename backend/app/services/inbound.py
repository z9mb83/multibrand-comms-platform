"""Inbound reply ingestion for the unified inbox.

Brand is identified ONLY from the receiving sender account (the mailbox / WhatsApp number /
SMS header that received the message). A contact's email or phone never moves a reply into
another brand.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, DailyTask, InboundMessage, OutboundMessage, SenderAccount
from app.models.enums import ConversationStatus, ReplyClass, TaskStatus
from app.services.common import DomainError, audit, normalize_email, normalize_phone, now_utc
from app.services.contacts import find_brand_contact, unsubscribe, upsert_brand_contact
from app.services.followups import pause_for_conversation

UNSUB_RE = re.compile(r"^\s*(stop|unsubscribe|stop all|opt[ -]?out|remove me|cancel)\b", re.I)


@dataclass
class InboundEvent:
    channel: str
    to_address: str | None  # receiving account address (email / display number / header)
    from_address: str
    provider_message_id: str
    body: str | None = None
    subject: str | None = None
    thread_id: str | None = None
    in_reply_to: str | None = None
    received_at: datetime | None = None
    receiving_account_id: int | None = None  # when the webhook already resolved it (e.g. phone_number_id)
    raw: dict = field(default_factory=dict)


def classify(text: str | None) -> tuple[str, float]:
    """Lightweight rule-based classifier. An LLM classifier can replace this; humans can always override."""
    t = (text or "").lower()
    if UNSUB_RE.search(t) or "unsubscribe" in t:
        return ReplyClass.UNSUBSCRIBE, 0.95
    if any(k in t for k in ("not interested", "no thanks", "no thank you", "don't contact", "do not contact")):
        return ReplyClass.NOT_INTERESTED, 0.8
    if any(k in t for k in ("call me", "callback", "call back", "give me a call", "phone me")):
        return ReplyClass.CALLBACK, 0.75
    if any(k in t for k in ("interested", "sounds good", "let's talk", "lets talk", "book", "yes please")):
        return ReplyClass.INTERESTED, 0.7
    if "?" in t or any(k in t for k in ("more info", "details", "price", "pricing", "brochure", "how much")):
        return ReplyClass.NEEDS_INFO, 0.6
    return ReplyClass.OTHER, 0.3


def resolve_account(db: Session, ev: InboundEvent) -> SenderAccount:
    if ev.receiving_account_id:
        acc = db.get(SenderAccount, ev.receiving_account_id)
        if acc is None or acc.channel != ev.channel:
            raise DomainError("receiving account not found", 404)
        return acc
    addr = (ev.to_address or "").strip()
    if ev.channel == "email":
        addr = addr.lower()
    else:
        addr = normalize_phone(addr) or addr
    accounts = db.scalars(select(SenderAccount).where(SenderAccount.channel == ev.channel,
                                                      SenderAccount.address == addr)).all()
    if not accounts:
        raise DomainError(f"no sender account receives {ev.channel} at {addr}", 404)
    if len(accounts) > 1:
        # Same address configured in several brands: only a thread match may disambiguate.
        if ev.thread_id:
            conv = db.scalars(select(Conversation).where(Conversation.external_thread_id == ev.thread_id,
                                                         Conversation.sender_account_id.in_([a.id for a in accounts]))).first()
            if conv:
                return db.get(SenderAccount, conv.sender_account_id)
        raise DomainError("receiving address is shared by several brands; cannot assign safely", 409)
    return accounts[0]


def ingest(db: Session, ev: InboundEvent) -> InboundMessage:
    existing = db.scalars(select(InboundMessage).where(InboundMessage.channel == ev.channel,
                                                       InboundMessage.provider_message_id == ev.provider_message_id)).first()
    if existing:
        return existing  # webhook retries are idempotent

    # 1-2. brand + sender account
    account = resolve_account(db, ev)
    brand_id = account.brand_id

    # 3. contact within that brand only
    email = normalize_email(ev.from_address) if ev.channel == "email" else None
    phone = normalize_phone(ev.from_address) if ev.channel != "email" else None
    bc = find_brand_contact(db, brand_id, email, phone)
    if bc is None:
        bc = upsert_brand_contact(db, brand_id, email=email, phone=phone, contact_name=None)
        audit(db, "contact.created_from_inbound", brand_id=brand_id, entity_type="contact", entity_id=bc.contact_id)

    # 4. conversation: thread id -> In-Reply-To -> latest open conversation on this account -> new
    conv = None
    if ev.thread_id:
        conv = db.scalars(select(Conversation).where(Conversation.brand_id == brand_id,
                                                     Conversation.external_thread_id == ev.thread_id)).first()
    if conv is None and ev.in_reply_to:
        conv = db.scalars(select(Conversation).where(Conversation.brand_id == brand_id,
                                                     Conversation.last_message_ref == ev.in_reply_to)).first()
        if conv is None:
            om = db.scalars(select(OutboundMessage).where(OutboundMessage.brand_id == brand_id,
                                                          OutboundMessage.provider_message_id == ev.in_reply_to)).first()
            conv = db.get(Conversation, om.conversation_id) if om and om.conversation_id else None
    if conv is None:
        conv = db.scalars(select(Conversation).where(Conversation.brand_id == brand_id,
                                                     Conversation.contact_id == bc.contact_id,
                                                     Conversation.channel == ev.channel,
                                                     Conversation.sender_account_id == account.id)
                          .order_by(Conversation.id.desc()).limit(1)).first()
    if conv is None:
        conv = Conversation(brand_id=brand_id, contact_id=bc.contact_id, channel=ev.channel,
                            sender_account_id=account.id, external_thread_id=ev.thread_id, subject=ev.subject,
                            status=ConversationStatus.OPEN, owner_user_id=bc.owner_user_id)
        db.add(conv)
        db.flush()
    if conv.brand_id != brand_id or conv.contact_id != bc.contact_id:
        raise DomainError("conversation/contact mismatch", 409)

    # 5-6. save message (with classification)
    label, conf = classify(ev.body)
    is_unsub = label == ReplyClass.UNSUBSCRIBE
    msg = InboundMessage(brand_id=brand_id, conversation_id=conv.id, contact_id=bc.contact_id,
                         sender_account_id=account.id, campaign_id=conv.campaign_id, channel=ev.channel,
                         from_address=email or phone or ev.from_address, subject=ev.subject, body=ev.body,
                         provider_message_id=ev.provider_message_id, provider_thread_id=ev.thread_id,
                         in_reply_to=ev.in_reply_to, received_at=ev.received_at or now_utc(),
                         is_unsubscribe=is_unsub, classification=label, classification_source="rule",
                         classification_confidence=conf, raw=ev.raw)
    db.add(msg)

    # 7. conversation status
    conv.status = ConversationStatus.NEEDS_REPLY
    conv.last_inbound_at = msg.received_at
    conv.unread_count += 1
    conv.external_thread_id = conv.external_thread_id or ev.thread_id

    # follow-ups pause immediately on any reply, pending human review
    conv.followups_paused = True
    pause_for_conversation(db, conv.id, reason="reply")

    if is_unsub:
        unsubscribe(db, brand_id, bc.contact_id, ev.channel, email or phone, reason="unsubscribe")

    # 8. notify assigned owner via a task on their board
    owner = conv.owner_user_id or bc.owner_user_id
    if not is_unsub:
        db.add(DailyTask(brand_id=brand_id, contact_id=bc.contact_id, company_name=bc.company_name,
                         assigned_user_id=owner, description=f"Reply received ({label}) from "
                         f"{bc.contact_name or bc.email or bc.phone} — respond", priority="high",
                         due_at=now_utc(), status=TaskStatus.PENDING, conversation_id=conv.id, source="system"))
    db.flush()
    audit(db, "inbound.received", brand_id=brand_id, entity_type="inbound_message", entity_id=msg.id,
          channel=ev.channel, classification=label)
    return msg


def set_classification(db: Session, inbound_id: int, label: str, user_id: int) -> InboundMessage:
    valid = {c.value for c in ReplyClass}
    if label not in valid:
        raise DomainError(f"classification must be one of {sorted(valid)}")
    msg = db.get(InboundMessage, inbound_id)
    if msg is None:
        raise DomainError("message not found", 404)
    msg.classification, msg.classification_source, msg.classification_confidence = label, "human", 1.0
    audit(db, "inbound.classified", user_id=user_id, brand_id=msg.brand_id, entity_type="inbound_message",
          entity_id=msg.id, label=label)
    return msg


def apply_delivery_status(db: Session, channel: str, provider_message_id: str, status: str) -> bool:
    msg = db.scalars(select(OutboundMessage).where(OutboundMessage.channel == channel,
                                                   OutboundMessage.provider_message_id == provider_message_id)).first()
    if msg is None:
        return False
    order = ["sent", "delivered", "read"]
    if status in order and (msg.status not in order or order.index(status) > order.index(msg.status)):
        msg.status = status
        if status == "delivered":
            msg.delivered_at = now_utc()
    elif status in ("failed", "undelivered", "bounced"):
        msg.status, msg.last_error = "failed", f"provider status: {status}"
        if status == "bounced" and channel == "email":
            from app.models import Suppression
            db.add(Suppression(brand_id=msg.brand_id, channel=channel, address=msg.to_address, reason="hard_bounce"))
    return True
