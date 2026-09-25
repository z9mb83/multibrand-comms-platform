"""Idempotent outbound dispatch.

process_outbound(message_id) can be called any number of times (Cloud Tasks retries,
duplicate enqueues, manual retries); a message is handed to a provider at most once
per attempt and never again after it is sent.
"""
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import BrandContact, Campaign, Conversation, MessageAttempt, MessageTemplate, OutboundMessage, SenderAccount
from app.models.enums import CampaignStatus, Channel, ConversationStatus, MessageStatus, RoutingMode
from app.providers.base import SendRequest
from app.providers.registry import get_provider
from app.services.common import DomainError, audit, now_utc
from app.services.contacts import check_can_send
from app.services.routing import assign_sender

MAX_ATTEMPTS = 5
TERMINAL = {MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.READ, MessageStatus.CANCELLED,
            MessageStatus.SUPPRESSED}


def find_open_conversation(db: Session, brand_id: int, contact_id: int, channel: str) -> Conversation | None:
    return db.scalars(select(Conversation)
                      .where(Conversation.brand_id == brand_id, Conversation.contact_id == contact_id,
                             Conversation.channel == channel, Conversation.status != ConversationStatus.CLOSED)
                      .order_by(Conversation.id.desc()).limit(1)).first()


def create_message(db: Session, *, brand_id: int, contact_id: int, channel: str, to_address: str,
                   subject: str | None = None, body: str | None = None, template_id: int | None = None,
                   template_params: dict | None = None, campaign_id: int | None = None,
                   routing_mode: str = RoutingMode.AUTO, requested_sender_id: int | None = None,
                   scheduled_at=None, status: str = MessageStatus.DRAFT, idempotency_key: str | None = None,
                   conversation_id: int | None = None, follow_up_task_id: int | None = None) -> OutboundMessage:
    key = idempotency_key or f"msg-{uuid.uuid4().hex}"
    existing = db.scalars(select(OutboundMessage).where(OutboundMessage.idempotency_key == key)).first()
    if existing:
        if existing.brand_id != brand_id:
            raise DomainError("idempotency key belongs to another brand", 409)
        return existing
    if requested_sender_id:
        acc = db.get(SenderAccount, requested_sender_id)
        if acc is None or acc.brand_id != brand_id or acc.channel != channel:
            raise DomainError("requested sender does not belong to this brand/channel")
    if campaign_id:
        camp = db.get(Campaign, campaign_id)
        if camp is None or camp.brand_id != brand_id:
            raise DomainError("campaign does not belong to this brand")
    if template_id:
        tpl = db.get(MessageTemplate, template_id)
        if tpl is None or tpl.brand_id != brand_id:
            raise DomainError("template does not belong to this brand")
    msg = OutboundMessage(brand_id=brand_id, contact_id=contact_id, channel=channel, to_address=to_address,
                          subject=subject, body=body, template_id=template_id, template_params=template_params or {},
                          campaign_id=campaign_id, routing_mode=routing_mode, requested_sender_id=requested_sender_id,
                          scheduled_at=scheduled_at, status=status, idempotency_key=key,
                          conversation_id=conversation_id, follow_up_task_id=follow_up_task_id)
    db.add(msg)
    db.flush()
    return msg


def _prepare(db: Session, message_id: int) -> tuple[str, SendRequest | None]:
    """Phase 1 (in a transaction): lock, validate, route, mark SENDING."""
    s = get_settings()
    msg = db.scalars(select(OutboundMessage).where(OutboundMessage.id == message_id)
                     .with_for_update(skip_locked=True)).first()
    if msg is None:
        return "locked_or_missing", None
    now = now_utc()
    if msg.status in TERMINAL:
        return "already_done", None
    if msg.status == MessageStatus.SENDING:
        if msg.sending_started_at and now - msg.sending_started_at < timedelta(seconds=s.sending_lease_seconds):
            return "in_flight", None
        # lease expired: we cannot know whether the provider accepted it -> human review, never blind resend
        msg.status, msg.hold_reason = MessageStatus.UNKNOWN, "sending_lease_expired"
        return "unknown", None
    if msg.status != MessageStatus.QUEUED:
        return f"not_queued:{msg.status}", None
    if msg.scheduled_at and msg.scheduled_at > now:
        return "not_due", None

    campaign = db.get(Campaign, msg.campaign_id) if msg.campaign_id else None
    if campaign and campaign.status not in (CampaignStatus.APPROVED, CampaignStatus.RUNNING):
        return f"campaign_{campaign.status}", None

    reason = check_can_send(db, msg.brand_id, msg.contact_id, msg.channel, msg.to_address)
    if reason:
        msg.status, msg.hold_reason = MessageStatus.SUPPRESSED, reason
        audit(db, "message.suppressed", brand_id=msg.brand_id, entity_type="outbound_message", entity_id=msg.id,
              reason=reason)
        return "suppressed", None

    bc = db.scalars(select(BrandContact).where(BrandContact.brand_id == msg.brand_id,
                                               BrandContact.contact_id == msg.contact_id)).first()
    conv = db.get(Conversation, msg.conversation_id) if msg.conversation_id else None
    if conv is None and msg.channel == Channel.EMAIL:
        conv = find_open_conversation(db, msg.brand_id, msg.contact_id, msg.channel)
    if conv is not None and conv.brand_id != msg.brand_id:
        raise DomainError("conversation brand mismatch", 409)

    decision = assign_sender(db, brand_id=msg.brand_id, channel=msg.channel, mode=msg.routing_mode,
                             fixed_sender_id=msg.requested_sender_id, brand_contact=bc, conversation=conv,
                             campaign=campaign, message_id=msg.id)
    if decision.outcome == "held":
        msg.status, msg.hold_reason = MessageStatus.HELD_FOR_REVIEW, decision.reason
        return "held", None
    if decision.outcome == "no_eligible":
        msg.hold_reason = decision.reason  # stays QUEUED, retried later
        return "waiting_for_sender", None

    sender = decision.sender
    assert sender is not None
    msg.sender_account_id = sender.id
    msg.status, msg.hold_reason = MessageStatus.SENDING, None
    msg.sending_started_at = now
    msg.attempt_count += 1
    if conv is not None:
        msg.conversation_id = conv.id

    tpl = db.get(MessageTemplate, msg.template_id) if msg.template_id else None
    params = dict(msg.template_params or {})
    if tpl and tpl.external_template_id and msg.channel == Channel.SMS:
        params.setdefault("dlt_template_id", tpl.external_template_id)
    subject = msg.subject or (tpl.subject if tpl else None)
    if conv is not None and msg.channel == Channel.EMAIL and conv.subject and not subject:
        subject = conv.subject if conv.subject.lower().startswith("re:") else f"Re: {conv.subject}"
    req = SendRequest(
        channel=msg.channel, from_address=sender.address, from_name=sender.display_name, to_address=msg.to_address,
        subject=subject, body=msg.body or (tpl.body if tpl else None),
        template_name=(tpl.external_template_id or tpl.name) if (tpl and msg.channel == Channel.WHATSAPP) else None,
        template_params=params, idempotency_key=msg.idempotency_key,
        thread_id=conv.external_thread_id if conv else None,
        in_reply_to=conv.last_message_ref if conv else None,
        list_unsubscribe=f"<mailto:{sender.address}?subject=unsubscribe>" if msg.channel == Channel.EMAIL else None,
        sender_config=sender.provider_config or {}, secret_ref=sender.secret_ref,
    )
    req.sender_config = {**req.sender_config, "_provider": sender.provider}
    return "ready", req


def _finish(db: Session, message_id: int, result, provider_name: str) -> str:
    msg = db.scalars(select(OutboundMessage).where(OutboundMessage.id == message_id).with_for_update()).one()
    now = now_utc()
    db.add(MessageAttempt(outbound_message_id=msg.id, sender_account_id=msg.sender_account_id,
                          attempt_no=msg.attempt_count, status="sent" if result.ok else "failed",
                          provider_message_id=result.provider_message_id, error=result.error,
                          meta={"provider": provider_name}))
    if result.ok:
        msg.status, msg.sent_at = MessageStatus.SENT, now
        msg.provider_message_id, msg.provider_thread_id = result.provider_message_id, result.thread_id
        msg.last_error = None
        _update_conversation(db, msg, result, now)
        from app.services.followups import schedule_initial_followup
        schedule_initial_followup(db, msg)
        return "sent"
    msg.last_error = result.error
    if result.retryable and msg.attempt_count < MAX_ATTEMPTS:
        msg.status = MessageStatus.QUEUED
        msg.scheduled_at = now + timedelta(minutes=2 ** msg.attempt_count)
        return "retry_scheduled"
    msg.status = MessageStatus.FAILED
    return "failed"


def _update_conversation(db: Session, msg: OutboundMessage, result, now) -> None:
    conv = db.get(Conversation, msg.conversation_id) if msg.conversation_id else None
    if conv is None:
        conv = Conversation(brand_id=msg.brand_id, contact_id=msg.contact_id, channel=msg.channel,
                            sender_account_id=msg.sender_account_id, campaign_id=msg.campaign_id,
                            subject=msg.subject, status=ConversationStatus.OPEN)
        db.add(conv)
        db.flush()
        msg.conversation_id = conv.id
    if conv.sender_account_id is None:
        conv.sender_account_id = msg.sender_account_id
    conv.campaign_id = conv.campaign_id or msg.campaign_id
    conv.external_thread_id = result.thread_id or conv.external_thread_id
    conv.last_message_ref = result.message_ref or conv.last_message_ref
    conv.last_outbound_at = now
    if conv.status == ConversationStatus.NEEDS_REPLY:
        conv.status = ConversationStatus.REPLIED


def process_outbound(factory: sessionmaker, message_id: int) -> str:
    with factory() as db:
        with db.begin():
            outcome, req = _prepare(db, message_id)
        if outcome != "ready" or req is None:
            return outcome
    provider_name = req.sender_config.pop("_provider", "mock")
    provider = get_provider(req.channel, provider_name)
    result = provider.send(req)  # network call outside any DB transaction
    with factory() as db:
        with db.begin():
            return _finish(db, message_id, result, provider_name)


def due_message_ids(db: Session, limit: int = 200) -> list[int]:
    now = now_utc()
    q = (select(OutboundMessage.id)
         .where(OutboundMessage.status == MessageStatus.QUEUED,
                (OutboundMessage.scheduled_at.is_(None)) | (OutboundMessage.scheduled_at <= now))
         .order_by(OutboundMessage.id).limit(limit))
    return list(db.scalars(q))
