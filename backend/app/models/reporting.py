from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class MasterReportSettings(TimestampMixin, Base):
    """Singleton (id=1) Master Control Center configuration."""

    __tablename__ = "master_report_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    master_email: Mapped[str | None] = mapped_column(String(255))
    master_mobile: Mapped[str | None] = mapped_column(String(32))
    mobile_opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notification_channel: Mapped[str] = mapped_column(String(30), default="email")  # email|whatsapp|sms|email+whatsapp|email+sms
    morning_time: Mapped[str] = mapped_column(String(5), default="09:30")
    evening_time: Mapped[str] = mapped_column(String(5), default="18:30")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    working_days: Mapped[list] = mapped_column(JSONB, default=lambda: [0, 1, 2, 3, 4, 5])  # Mon=0
    brand_preferences: Mapped[dict] = mapped_column(JSONB, default=dict)  # {brand_id: {"include": true}}
    emergency_alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    report_recipients: Mapped[list] = mapped_column(JSONB, default=list)  # extra emails
    attach_spreadsheet: Mapped[bool] = mapped_column(Boolean, default=False)
    # Dedicated notification identities, independent of marketing rotation
    notification_email_sender_id: Mapped[int | None] = mapped_column(ForeignKey("sender_accounts.id", ondelete="SET NULL"))
    notification_whatsapp_sender_id: Mapped[int | None] = mapped_column(
        ForeignKey("sender_accounts.id", ondelete="SET NULL")
    )
    notification_sms_sender_id: Mapped[int | None] = mapped_column(ForeignKey("sender_accounts.id", ondelete="SET NULL"))
    whatsapp_report_template: Mapped[str | None] = mapped_column(String(120))
    task_carry_forward: Mapped[bool] = mapped_column(Boolean, default=True)


class MasterReport(Base):
    __tablename__ = "master_reports"
    __table_args__ = (UniqueConstraint("kind", "report_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # morning|evening
    report_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="generated")  # generated|delivered|partially_delivered|failed
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    html: Mapped[str | None] = mapped_column(Text)
    mobile_text: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("master_reports.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="report")  # report|emergency|owner_alert
    channel: Mapped[str] = mapped_column(String(16))
    recipient: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
