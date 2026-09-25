from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import BrandCtx, accessible_brand_ids, brand_ctx, require_super_admin
from app.core.db import get_db
from app.models import BrandContact, Campaign, ContactConsent, OutboundMessage, User
from app.models.enums import CampaignStatus, MessageStatus, Role
from app.schemas.api import CampaignAction, CampaignIn, CampaignOut, ComposeIn, OutboundOut, RecipientsIn
from app.services import campaigns as svc
from app.services.common import audit
from app.services.dispatch import create_message
from app.services.queue import enqueue_dispatch

router = APIRouter(prefix="/brands/{brand_id}", tags=["campaigns"])

APPROVE_ACTIONS = {"approve", "launch", "resume", "emergency_stop"}


def _campaign(db: Session, brand_id: int, cid: int) -> Campaign:
    c = db.get(Campaign, cid)
    if c is None or c.brand_id != brand_id:
        raise HTTPException(404, "campaign not found")
    return c


@router.get("/campaigns", response_model=list[CampaignOut])
def list_campaigns(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    return db.scalars(select(Campaign).where(Campaign.brand_id == ctx.brand_id).order_by(Campaign.id.desc())).all()


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
def create_campaign(body: CampaignIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.MANAGER)
    if db.scalar(select(Campaign.id).where(Campaign.brand_id == ctx.brand_id, Campaign.name == body.name)):
        raise HTTPException(409, "campaign name already used in this brand")
    c = Campaign(brand_id=ctx.brand_id, created_by=ctx.user.id, status=CampaignStatus.DRAFT, **body.model_dump())
    db.add(c)
    db.flush()
    audit(db, "campaign.created", user_id=ctx.user.id, brand_id=ctx.brand_id, entity_type="campaign", entity_id=c.id)
    db.commit()
    return c


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: int, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    c = _campaign(db, ctx.brand_id, campaign_id)
    return {"campaign": CampaignOut.model_validate(c), "stats": svc.stats(db, c.id)}


@router.post("/campaigns/{campaign_id}/recipients")
def add_recipients(campaign_id: int, body: RecipientsIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    """Adds draft messages for brand contacts (explicit ids or a consent-based segment)."""
    ctx.require(Role.MANAGER)
    c = _campaign(db, ctx.brand_id, campaign_id)
    if c.status not in (CampaignStatus.DRAFT, CampaignStatus.PENDING_APPROVAL):
        raise HTTPException(409, "recipients can only be added to draft campaigns")
    q = select(BrandContact).where(BrandContact.brand_id == ctx.brand_id)
    if body.contact_ids:
        q = q.where(BrandContact.contact_id.in_(body.contact_ids))
    elif body.segment.get("has_consent", True):
        q = q.join(ContactConsent, (ContactConsent.contact_id == BrandContact.contact_id) &
                   (ContactConsent.brand_id == ctx.brand_id) & (ContactConsent.channel == c.channel) &
                   (ContactConsent.status == "granted"))
    added = 0
    for bc in db.scalars(q):
        addr = bc.email if c.channel == "email" else bc.phone
        if not addr:
            continue
        create_message(db, brand_id=ctx.brand_id, contact_id=bc.contact_id, channel=c.channel, to_address=addr,
                       subject=c.subject, body=c.body, template_id=c.template_id, campaign_id=c.id,
                       routing_mode=c.default_routing_mode, scheduled_at=c.scheduled_at,
                       idempotency_key=f"camp-{c.id}-{bc.contact_id}")
        added += 1
    db.commit()
    return {"added": added, "stats": svc.stats(db, c.id)}


@router.post("/campaigns/{campaign_id}/actions")
def campaign_action(campaign_id: int, body: CampaignAction, ctx: BrandCtx = Depends(brand_ctx),
                    db: Session = Depends(get_db)):
    ctx.require(Role.MANAGER if body.action in APPROVE_ACTIONS else Role.AGENT)
    c = _campaign(db, ctx.brand_id, campaign_id)
    released = svc.transition(db, c, body.action, ctx.user.id, body.reason)
    db.commit()
    enqueue_dispatch(released)
    db.expire_all()
    return {"campaign": CampaignOut.model_validate(db.get(Campaign, c.id)), "stats": svc.stats(db, c.id)}


@router.get("/messages", response_model=list[OutboundOut])
def list_messages(status: str | None = None, campaign_id: int | None = None, limit: int = 100,
                  ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    q = select(OutboundMessage).where(OutboundMessage.brand_id == ctx.brand_id)
    if status:
        q = q.where(OutboundMessage.status == status)
    if campaign_id:
        q = q.where(OutboundMessage.campaign_id == campaign_id)
    return db.scalars(q.order_by(OutboundMessage.id.desc()).limit(min(limit, 500))).all()


@router.post("/compose", response_model=OutboundOut, status_code=201)
def compose(body: ComposeIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    """Single-recipient message. Sending it is an explicit user action (approval by the composer)."""
    ctx.require(Role.AGENT)
    bc = db.scalars(select(BrandContact).where(BrandContact.brand_id == ctx.brand_id,
                                               BrandContact.contact_id == body.contact_id)).first()
    if bc is None:
        raise HTTPException(404, "contact not found in this brand")
    addr = bc.email if body.channel == "email" else bc.phone
    if not addr:
        raise HTTPException(400, f"contact has no address for {body.channel}")
    mode = "FIXED" if body.sender_id and body.routing_mode == "AUTO" else body.routing_mode
    msg = create_message(db, brand_id=ctx.brand_id, contact_id=bc.contact_id, channel=body.channel, to_address=addr,
                         subject=body.subject, body=body.body, template_id=body.template_id, routing_mode=mode,
                         requested_sender_id=body.sender_id, scheduled_at=body.scheduled_at,
                         status=MessageStatus.QUEUED if body.send_now else MessageStatus.DRAFT,
                         idempotency_key=body.idempotency_key)
    audit(db, "message.composed", user_id=ctx.user.id, brand_id=ctx.brand_id, entity_type="outbound_message",
          entity_id=msg.id)
    db.commit()
    if body.send_now:
        enqueue_dispatch([msg.id])
    db.expire_all()
    return db.get(OutboundMessage, msg.id)


class ReleaseIn(BaseModel):
    sender_id: int | None = None


@router.post("/messages/{message_id}/release", response_model=OutboundOut)
def release_held(message_id: int, body: ReleaseIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    """Human review of a held message: optionally choose a different sender explicitly, then requeue."""
    ctx.require(Role.MANAGER)
    m = db.get(OutboundMessage, message_id)
    if m is None or m.brand_id != ctx.brand_id:
        raise HTTPException(404, "message not found")
    if m.status not in (MessageStatus.HELD_FOR_REVIEW, MessageStatus.FAILED, MessageStatus.UNKNOWN):
        raise HTTPException(409, f"message is {m.status}")
    if body.sender_id:
        from app.models import SenderAccount
        s = db.get(SenderAccount, body.sender_id)
        if s is None or s.brand_id != ctx.brand_id or s.channel != m.channel:
            raise HTTPException(400, "sender does not belong to this brand/channel")
        m.requested_sender_id, m.routing_mode = s.id, "FIXED"
    m.status, m.hold_reason = MessageStatus.QUEUED, None
    audit(db, "message.released", user_id=ctx.user.id, brand_id=ctx.brand_id, entity_type="outbound_message",
          entity_id=m.id, sender_id=body.sender_id)
    db.commit()
    enqueue_dispatch([m.id])
    db.expire_all()
    return db.get(OutboundMessage, m.id)


global_router = APIRouter(tags=["campaigns"])


class EmergencyIn(BaseModel):
    reason: str
    brand_ids: list[int] | None = None


@global_router.post("/emergency-stop")
def emergency_stop(body: EmergencyIn, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    ids = body.brand_ids if body.brand_ids is not None else list(accessible_brand_ids(db, admin))
    n = svc.emergency_stop_all(db, admin.id, ids, body.reason)
    db.commit()
    return {"stopped_campaigns": n}
