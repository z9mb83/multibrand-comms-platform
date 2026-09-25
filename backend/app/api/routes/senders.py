from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import BrandCtx, brand_ctx
from app.core.db import get_db
from app.models import SenderAccount, SenderAssignment
from app.models.enums import Role
from app.providers.registry import get_provider
from app.schemas.api import SenderIn, SenderOut, SenderUpdate
from app.services.common import audit, normalize_email, normalize_phone, now_utc
from app.services.routing import effective_daily_limit, ineligibility_reason, preview_next_sender

router = APIRouter(prefix="/brands/{brand_id}/senders", tags=["senders"])


def _get(db: Session, brand_id: int, sender_id: int) -> SenderAccount:
    s = db.get(SenderAccount, sender_id)
    if s is None or s.brand_id != brand_id:
        raise HTTPException(404, "sender not found")
    return s


@router.get("", response_model=list[SenderOut])
def list_senders(channel: str | None = None, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    q = select(SenderAccount).where(SenderAccount.brand_id == ctx.brand_id)
    if channel:
        q = q.where(SenderAccount.channel == channel)
    return db.scalars(q.order_by(SenderAccount.channel, SenderAccount.rotation_order, SenderAccount.id)).all()


@router.post("", response_model=SenderOut, status_code=201)
def create_sender(body: SenderIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    addr = normalize_email(body.address) if body.channel == "email" else (
        normalize_phone(body.address) if body.channel == "whatsapp" else body.address.strip().upper())
    if not addr:
        raise HTTPException(400, "invalid address for channel")
    if db.scalar(select(SenderAccount.id).where(SenderAccount.brand_id == ctx.brand_id,
                                                SenderAccount.channel == body.channel, SenderAccount.address == addr)):
        raise HTTPException(409, "sender already exists for this brand")
    s = SenderAccount(brand_id=ctx.brand_id, **{**body.model_dump(), "address": addr})
    db.add(s)
    db.flush()
    audit(db, "sender.created", user_id=ctx.user.id, brand_id=ctx.brand_id, entity_type="sender", entity_id=s.id)
    db.commit()
    return s


@router.patch("/{sender_id}", response_model=SenderOut)
def update_sender(sender_id: int, body: SenderUpdate, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    s = _get(db, ctx.brand_id, sender_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(s, k, v)
    audit(db, "sender.updated", user_id=ctx.user.id, brand_id=ctx.brand_id, entity_type="sender", entity_id=s.id,
          changes={k: v for k, v in changes.items() if k != "secret_ref"})
    db.commit()
    return s


@router.post("/{sender_id}/health-check", response_model=SenderOut)
def health_check(sender_id: int, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    s = _get(db, ctx.brand_id, sender_id)
    res = get_provider(s.channel, s.provider).check_health(s.address, s.provider_config or {}, s.secret_ref)
    s.health_status = "healthy" if res.healthy else "failing"
    s.last_health_check_at = now_utc()
    s.last_error = None if res.healthy else res.detail
    if not res.healthy:
        s.connection_status = "error"
    db.commit()
    return s


@router.get("/routing/overview")
def routing_overview(channel: str = Query("email"), ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    nxt, rejected = preview_next_sender(db, ctx.brand_id, channel)
    accounts = db.scalars(select(SenderAccount).where(SenderAccount.brand_id == ctx.brand_id,
                                                      SenderAccount.channel == channel,
                                                      SenderAccount.is_notification_sender.is_(False))
                          .order_by(SenderAccount.rotation_order, SenderAccount.id)).all()
    items = []
    for a in accounts:
        reason = ineligibility_reason(a)
        items.append({"id": a.id, "address": a.address, "status": a.status, "connection_status": a.connection_status,
                      "health_status": a.health_status, "sent_today": a.sent_today,
                      "effective_daily_limit": effective_daily_limit(a), "eligible": reason is None,
                      "reason": reason})
        db.expire(a)
    history = db.execute(select(SenderAssignment, SenderAccount.address)
                         .outerjoin(SenderAccount, SenderAccount.id == SenderAssignment.sender_account_id)
                         .where(SenderAssignment.brand_id == ctx.brand_id, SenderAssignment.channel == channel)
                         .order_by(SenderAssignment.id.desc()).limit(25)).all()
    return {
        "channel": channel,
        "active_senders": sum(1 for i in items if i["eligible"]),
        "total_senders": len(items),
        "next_sender": {"id": nxt.id, "address": nxt.address} if nxt else None,
        "accounts": items,
        "history": [{"id": h.id, "message_id": h.outbound_message_id, "mode": h.routing_mode, "outcome": h.outcome,
                     "sender": addr, "reason": h.reason, "at": h.created_at} for h, addr in history],
    }
