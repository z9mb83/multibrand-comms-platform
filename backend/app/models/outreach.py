"""Email outreach: the Sheet + Cloud Run automation, stored in Postgres.

Each table mirrors a tab of the original Google Sheet:
  OutreachSettings   -> "<Brand> Email list" settings block (A2:C10)
  OutreachMailbox    -> "<Brand> Email list" mailbox rows (A13:M)
  OutreachLead       -> lead tab (Person Name .. Follow-up Status)
  OutreachSendLog    -> "Send Log" tab
  OutreachSuppression-> "Suppression" tab
OutreachGlobal replaces the round-robin JSON/Firestore document and runtime .env values.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class OutreachSettings(TimestampMixin, Base):
    __tablename__ = "outreach_settings"

    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Starts ON, like initial_emergency_stop: nothing sends until someone switches it off after testing.
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=True)
    default_hourly_limit: Mapped[int] = mapped_column(Integer, default=1)
    default_daily_limit: Mapped[int] = mapped_column(Integer, default=3)
    minimum_gap_minutes: Mapped[int] = mapped_column(Integer, default=25)
    cc_email: Mapped[str] = mapped_column(String(255), default="")
    sending_start_time: Mapped[str] = mapped_column(String(5), default="10:00")
    sending_end_time: Mapped[str] = mapped_column(String(5), default="18:00")
    brand_facts: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    signature_name: Mapped[str] = mapped_column(String(120), default="Ajay Veer")
    signature_title: Mapped[str] = mapped_column(String(120), default="Sales Executive")
    run_order: Mapped[int] = mapped_column(Integer, default=0)  # position in BRAND_ORDER rotation


class OutreachMailbox(TimestampMixin, Base):
    """Outreach control row for one connected mailbox (the Sheet's mailbox table)."""

    __tablename__ = "outreach_mailboxes"

    sender_account_id: Mapped[int] = mapped_column(ForeignKey("sender_accounts.id", ondelete="CASCADE"), primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stage: Mapped[str] = mapped_column(String(20), default="Stage 1")
    daily_limit: Mapped[int | None] = mapped_column(Integer)  # blank -> brand default
    hourly_limit: Mapped[int | None] = mapped_column(Integer)
    clean_days: Mapped[int] = mapped_column(Integer, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_eligible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_reason: Mapped[str] = mapped_column(String(255), default="")
    last_result: Mapped[str] = mapped_column(String(255), default="")


class OutreachLead(TimestampMixin, Base):
    __tablename__ = "outreach_leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    person_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(320), default="", index=True)
    details: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="Pending", index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(Text, default="")
    sent_from: Mapped[str] = mapped_column(String(320), default="")
    approval: Mapped[str] = mapped_column(String(10), default="")  # "Yes" approves
    gmail_message_id: Mapped[str] = mapped_column(String(120), default="")
    gmail_thread_id: Mapped[str] = mapped_column(String(120), default="")
    follow_up_status: Mapped[str] = mapped_column(String(20), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class OutreachSendLog(Base):
    __tablename__ = "outreach_send_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("outreach_leads.id", ondelete="SET NULL"))
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    sender_email: Mapped[str] = mapped_column(String(320))
    lead_email: Mapped[str] = mapped_column(String(320), index=True)
    subject: Mapped[str] = mapped_column(String(500), default="")
    gmail_message_id: Mapped[str] = mapped_column(String(120), default="")
    gmail_thread_id: Mapped[str] = mapped_column(String(120), default="")
    # Sent | Failed | Sending (claimed before the Gmail call; a stuck "Sending" row means delivery is uncertain)
    result: Mapped[str] = mapped_column(String(20))
    error: Mapped[str] = mapped_column(Text, default="")
    daily_limit: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(20), default="")
    imported: Mapped[bool] = mapped_column(Boolean, default=False)  # history brought over from the Sheet


class OutreachSuppression(Base):
    __tablename__ = "outreach_suppressions"
    __table_args__ = (UniqueConstraint("brand_id", "email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutreachGlobal(Base):
    """Single row (id=1): runtime settings from .env plus the round-robin cursor."""

    __tablename__ = "outreach_global"

    id: Mapped[int] = mapped_column(primary_key=True)
    next_start_index: Mapped[int] = mapped_column(Integer, default=0)
    cross_brand_duplicate_days: Mapped[int] = mapped_column(Integer, default=0)
    max_drafts_per_brand_per_run: Mapped[int] = mapped_column(Integer, default=20)
    openai_model: Mapped[str] = mapped_column(String(80), default="gpt-5-mini")
    reports_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    report_recipient: Mapped[str] = mapped_column(String(320), default="info@99marketingservices.com")
    report_sender: Mapped[str] = mapped_column(String(320), default="linkwithmarketing@gmail.com")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_summary: Mapped[str] = mapped_column(Text, default="")


class OutreachReportReceipt(Base):
    """Duplicate protection for the daily reports (automation_report_receipts in Firestore)."""

    __tablename__ = "outreach_report_receipts"
    __table_args__ = (UniqueConstraint("recipient", "slot", "report_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient: Mapped[str] = mapped_column(String(320))
    slot: Mapped[str] = mapped_column(String(10))  # morning | evening
    report_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="sending")  # sending | sent
    message_id: Mapped[str] = mapped_column(String(120), default="")
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
