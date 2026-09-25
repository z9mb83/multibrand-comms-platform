from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, utcnow


class Brand(TimestampMixin, Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="active")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    primary_color: Mapped[str | None] = mapped_column(String(16))
    master_email: Mapped[str | None] = mapped_column(String(255))  # brand's main mailbox (also a sender)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

    memberships: Mapped[list["BrandMembership"]] = relationship(back_populates="brand")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    full_name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Sign in with Google: stable Google account id, linked on first Google sign-in by verified email
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Optional second step: one-time code emailed after password / Google sign-in
    mfa_email_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    memberships: Mapped[list["BrandMembership"]] = relationship(back_populates="user")


class AuthChallenge(Base):
    """Short-lived email one-time code. Only a salted hash of the code is stored."""

    __tablename__ = "auth_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True, default="", server_default="")  # where the code was sent
    sender_account_id: Mapped[int | None] = mapped_column(ForeignKey("sender_accounts.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(20))  # login|email_login|signup_owner|enable_mfa|disable_mfa|verify_mailbox
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    method: Mapped[str] = mapped_column(String(20), default="password")  # password | google
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class BrandMembership(TimestampMixin, Base):
    __tablename__ = "brand_memberships"
    __table_args__ = (UniqueConstraint("user_id", "brand_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="agent")

    user: Mapped[User] = relationship(back_populates="memberships")
    brand: Mapped[Brand] = relationship(back_populates="memberships")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class MessageTemplate(TimestampMixin, Base):
    """Per-brand templates. For WhatsApp/SMS `external_template_id` is the approved Meta/DLT id."""

    __tablename__ = "message_templates"
    __table_args__ = (UniqueConstraint("brand_id", "channel", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(30), default="marketing")  # marketing|follow_up|notification
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(10), default="en")
    external_template_id: Mapped[str | None] = mapped_column(String(120))
    approval_status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|approved|rejected
    variables: Mapped[list] = mapped_column(JSONB, default=list)
