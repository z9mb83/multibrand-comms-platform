from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (UniqueConstraint("brand_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(10), default="bulk")  # bulk|single
    status: Mapped[str] = mapped_column(String(20), default="draft")
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("message_templates.id", ondelete="SET NULL"))
    default_routing_mode: Mapped[str] = mapped_column(String(10), default="AUTO")
    allowed_sender_ids: Mapped[list] = mapped_column(JSONB, default=list)  # empty = all brand senders
    segment: Mapped[dict] = mapped_column(JSONB, default=dict)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_reason: Mapped[str | None] = mapped_column(String(255))
    source_import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"))


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_brand_contact_channel", "brand_id", "contact_id", "channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(16))
    sender_account_id: Mapped[int | None] = mapped_column(ForeignKey("sender_accounts.id", ondelete="SET NULL"))
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"))
    external_thread_id: Mapped[str | None] = mapped_column(String(255), index=True)
    last_message_ref: Mapped[str | None] = mapped_column(String(500))  # RFC822 Message-ID for In-Reply-To
    subject: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="open")
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    followups_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unread_count: Mapped[int] = mapped_column(Integer, default=0)


class OutboundMessage(TimestampMixin, Base):
    __tablename__ = "outbound_messages"
    __table_args__ = (Index("ix_outbound_brand_status", "brand_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    follow_up_task_id: Mapped[int | None] = mapped_column(ForeignKey("follow_up_tasks.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(16))
    to_address: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("message_templates.id", ondelete="SET NULL"))
    template_params: Mapped[dict] = mapped_column(JSONB, default=dict)

    routing_mode: Mapped[str] = mapped_column(String(10), default="AUTO")
    requested_sender_id: Mapped[int | None] = mapped_column(ForeignKey("sender_accounts.id", ondelete="SET NULL"))
    sender_account_id: Mapped[int | None] = mapped_column(ForeignKey("sender_accounts.id", ondelete="SET NULL"))

    status: Mapped[str] = mapped_column(String(20), default="draft")
    hold_reason: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sending_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(255), index=True)
    provider_thread_id: Mapped[str | None] = mapped_column(String(255))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    import_row_ref: Mapped[str | None] = mapped_column(String(80))


class MessageAttempt(Base):
    __tablename__ = "message_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    outbound_message_id: Mapped[int] = mapped_column(ForeignKey("outbound_messages.id", ondelete="CASCADE"), index=True)
    sender_account_id: Mapped[int | None] = mapped_column(ForeignKey("sender_accounts.id", ondelete="SET NULL"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))  # sent|failed|skipped
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InboundMessage(Base):
    __tablename__ = "inbound_messages"
    __table_args__ = (UniqueConstraint("channel", "provider_message_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    sender_account_id: Mapped[int] = mapped_column(ForeignKey("sender_accounts.id", ondelete="CASCADE"))
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(16))
    from_address: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str] = mapped_column(String(255))
    provider_thread_id: Mapped[str | None] = mapped_column(String(255))
    in_reply_to: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    is_unsubscribe: Mapped[bool] = mapped_column(Boolean, default=False)
    classification: Mapped[str | None] = mapped_column(String(40))
    classification_source: Mapped[str | None] = mapped_column(String(10))  # ai|human|rule
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    handled: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
