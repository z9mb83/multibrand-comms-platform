from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class Contact(TimestampMixin, Base):
    """Global person identity keyed by normalised email/phone. Never exposed across brands;
    all brand-visible data lives on BrandContact."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(32), index=True)


class BrandContact(TimestampMixin, Base):
    __tablename__ = "brand_contacts"
    __table_args__ = (
        UniqueConstraint("brand_id", "contact_id"),
        Index("ix_brand_contacts_brand_email", "brand_id", "email"),
        Index("ix_brand_contacts_brand_phone", "brand_id", "phone"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    company_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")

    sticky_email_sender_id: Mapped[int | None] = mapped_column(ForeignKey("sender_accounts.id", ondelete="SET NULL"))
    sticky_whatsapp_sender_id: Mapped[int | None] = mapped_column(
        ForeignKey("sender_accounts.id", ondelete="SET NULL")
    )
    sticky_sms_sender_id: Mapped[int | None] = mapped_column(ForeignKey("sender_accounts.id", ondelete="SET NULL"))

    source_import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"))


class ContactConsent(Base):
    __tablename__ = "contact_consents"
    __table_args__ = (UniqueConstraint("brand_id", "contact_id", "channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # granted|withdrawn|unknown
    source: Mapped[str | None] = mapped_column(String(200))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Suppression(Base):
    """Per-brand, per-channel suppression list (unsubscribes, hard bounces, manual blocks)."""

    __tablename__ = "suppressions"
    __table_args__ = (UniqueConstraint("brand_id", "channel", "address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(String(40))  # unsubscribe|hard_bounce|manual|complaint
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
