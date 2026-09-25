"""Brand mailboxes: the master mailbox and the sub mailboxes campaign email is sent through.

A mailbox proves ownership either with a code emailed to it, or by connecting it with Google (which also grants
Gmail API access so the platform can actually send from it). Only connected, active mailboxes enter rotation.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import BrandCtx, brand_ctx
from app.core.config import get_settings
from app.core.db import get_db
from app.models import AuthChallenge, Brand, SenderAccount, SenderCredential
from app.models.enums import Role
from app.services import auth_flows
from app.services.common import audit, now_utc

router = APIRouter(prefix="/brands/{brand_id}/mailboxes", tags=["mailboxes"])


class MailboxIn(BaseModel):
    address: EmailStr
    role: str = Field(default="sub", pattern="^(master|sub)$")
    display_name: str | None = None
    daily_limit: int = Field(default=100, ge=1, le=2000)


class CodeIn(BaseModel):
    challenge_id: str
    code: str = Field(min_length=6, max_length=6)


def _out(a: SenderAccount, connected: bool) -> dict:
    return {"id": a.id, "address": a.address, "display_name": a.display_name, "role": "master" if a.is_master else "sub",
            "status": a.status, "connection_status": a.connection_status, "provider": a.provider,
            "verified": a.verified_at is not None, "verified_via": a.verified_via, "google_connected": connected,
            "sent_today": a.sent_today, "daily_limit": a.daily_limit, "last_error": a.last_error}


def _get(db: Session, brand_id: int, sid: int) -> SenderAccount:
    a = db.get(SenderAccount, sid)
    if a is None or a.brand_id != brand_id or a.channel != "email":
        raise HTTPException(404, "mailbox not found")
    return a


def list_for(db: Session, brand_id: int) -> list[dict]:
    rows = db.scalars(select(SenderAccount).where(SenderAccount.brand_id == brand_id, SenderAccount.channel == "email",
                                                  SenderAccount.is_notification_sender.is_(False))
                      .order_by(SenderAccount.is_master.desc(), SenderAccount.rotation_order, SenderAccount.id)).all()
    connected = set(db.scalars(select(SenderCredential.sender_account_id)
                               .where(SenderCredential.sender_account_id.in_([r.id for r in rows] or [0]))))
    return [_out(r, r.id in connected) for r in rows]


def add_mailbox(db: Session, brand: Brand, address: str, role: str, user_id: int, display_name: str | None = None,
                daily_limit: int = 100) -> SenderAccount:
    address = address.lower()
    if db.scalar(select(SenderAccount.id).where(SenderAccount.brand_id == brand.id, SenderAccount.channel == "email",
                                                SenderAccount.address == address)):
        raise HTTPException(409, "This mailbox is already added to the brand")
    order = len(db.scalars(select(SenderAccount.id).where(SenderAccount.brand_id == brand.id,
                                                          SenderAccount.channel == "email")).all()) + 1
    if role == "master":
        for m in db.scalars(select(SenderAccount).where(SenderAccount.brand_id == brand.id, SenderAccount.is_master)):
            m.is_master = False
        brand.master_email = address
    acc = SenderAccount(brand_id=brand.id, channel="email", address=address, display_name=display_name or brand.name,
                        provider="gmail", status="inactive", connection_status="pending", is_master=role == "master",
                        rotation_order=0 if role == "master" else order, daily_limit=daily_limit, hourly_limit=30,
                        provider_config={})
    db.add(acc)
    db.flush()
    audit(db, "mailbox.added", user_id=user_id, brand_id=brand.id, entity_type="sender", entity_id=acc.id, role=role)
    return acc


@router.get("")
def list_mailboxes(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    return list_for(db, ctx.brand_id)


@router.post("", status_code=201)
def create_mailbox(body: MailboxIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    acc = add_mailbox(db, db.get(Brand, ctx.brand_id), body.address, body.role, ctx.user.id, body.display_name,
                      body.daily_limit)
    db.commit()
    return _out(acc, False)


@router.post("/{sid}/make-master")
def make_master(sid: int, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    acc = _get(db, ctx.brand_id, sid)
    for m in db.scalars(select(SenderAccount).where(SenderAccount.brand_id == ctx.brand_id, SenderAccount.is_master)):
        m.is_master = False
    acc.is_master = True
    db.get(Brand, ctx.brand_id).master_email = acc.address
    db.commit()
    return list_for(db, ctx.brand_id)


@router.post("/{sid}/verify/start")
def verify_start(sid: int, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    acc = _get(db, ctx.brand_id, sid)
    ch = auth_flows.issue_code(db, None, "verify_mailbox", "email", email=acc.address, sender_account_id=acc.id)
    db.commit()
    return {"challenge_id": ch.id, "sent_to": acc.address}


@router.post("/{sid}/verify/confirm")
def verify_confirm(sid: int, body: CodeIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    acc = _get(db, ctx.brand_id, sid)
    pre = db.get(AuthChallenge, body.challenge_id)
    if pre is None or pre.sender_account_id != acc.id:
        raise HTTPException(400, "Invalid request")
    auth_flows.check_code(db, body.challenge_id, body.code, ("verify_mailbox",))
    acc.verified_at, acc.verified_via = now_utc(), "email_code"
    audit(db, "mailbox.verified", user_id=ctx.user.id, brand_id=ctx.brand_id, entity_type="sender", entity_id=acc.id)
    db.commit()
    return list_for(db, ctx.brand_id)


@router.post("/{sid}/google/start")
def google_connect_start(sid: int, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    """Returns the Google URL to open. Sets the browser-binding cookie on this (same-origin) response."""
    ctx.require(Role.BRAND_ADMIN)
    acc = _get(db, ctx.brand_id, sid)
    url, binding = auth_flows.google_start("mailbox", user_id=ctx.user.id, sender_account_id=acc.id,
                                           login_hint=acc.address)
    resp = JSONResponse({"url": url})
    resp.set_cookie("g_bind", binding, max_age=600, httponly=True, samesite="lax", path="/api/auth/google",
                    secure=get_settings().app_env == "production")
    return resp


@router.post("/{sid}/disconnect")
def disconnect(sid: int, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    acc = _get(db, ctx.brand_id, sid)
    for c in db.scalars(select(SenderCredential).where(SenderCredential.sender_account_id == acc.id)):
        db.delete(c)
    acc.connection_status, acc.secret_ref, acc.status = "disconnected", None, "inactive"
    audit(db, "mailbox.disconnected", user_id=ctx.user.id, brand_id=ctx.brand_id, entity_type="sender", entity_id=acc.id)
    db.commit()
    return list_for(db, ctx.brand_id)
