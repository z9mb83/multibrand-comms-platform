from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class SenderAccount(TimestampMixin, Base):
    """A sending identity: Gmail mailbox, WhatsApp Business number or SMS DLT header."""

    __tablename__ = "sender_accounts"
    __table_args__ = (UniqueConstraint("brand_id", "channel", "address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(30), default="mock")  # gmail|meta_cloud|sms_generic|mock
    address: Mapped[str] = mapped_column(String(255))  # email / E.164 number / DLT header
    display_name: Mapped[str | None] = mapped_column(String(160))

    status: Mapped[str] = mapped_column(String(20), default="inactive")
    connection_status: Mapped[str] = mapped_column(String(20), default="pending")
    paused_reason: Mapped[str | None] = mapped_column(String(255))
    rotation_order: Mapped[int] = mapped_column(Integer, default=0)
    is_notification_sender: Mapped[bool] = mapped_column(Boolean, default=False)

    # Limits: configured (ours) and provider-authorised (theirs). Effective = min of both.
    daily_limit: Mapped[int] = mapped_column(Integer, default=100)
    hourly_limit: Mapped[int] = mapped_column(Integer, default=30)
    provider_daily_limit: Mapped[int | None] = mapped_column(Integer)

    # Usage counters, bucketed lazily by date/hour in the brand timezone
    usage_date: Mapped[date | None] = mapped_column(Date)
    sent_today: Mapped[int] = mapped_column(Integer, default=0)
    usage_hour: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_this_hour: Mapped[int] = mapped_column(Integer, default=0)

    secret_ref: Mapped[str | None] = mapped_column(String(400))  # Secret Manager ref, never the secret
    provider_config: Mapped[dict] = mapped_column(JSONB, default=dict)  # phone_number_id, waba_id, dlt ids

    health_status: Mapped[str] = mapped_column(String(20), default="unknown")  # healthy|degraded|failing|unknown
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    bounce_count_7d: Mapped[int] = mapped_column(Integer, default=0)


class SenderRoutingState(Base):
    """Persistent round-robin cursor per (brand, channel). Row-locked during assignment."""

    __tablename__ = "sender_routing_state"
    __table_args__ = (UniqueConstraint("brand_id", "channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(16))
    last_sender_id: Mapped[int | None] = mapped_column(ForeignKey("sender_accounts.id", ondelete="SET NULL"))
    assignments_total: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SenderAssignment(Base):
    """Audit trail of every routing decision (assigned, held, rejected)."""

    __tablename__ = "sender_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    outbound_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("outbound_messages.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    routing_mode: Mapped[str] = mapped_column(String(10))
    sender_account_id: Mapped[int | None] = mapped_column(ForeignKey("sender_accounts.id", ondelete="SET NULL"))
    outcome: Mapped[str] = mapped_column(String(20))  # assigned|held|no_eligible
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
