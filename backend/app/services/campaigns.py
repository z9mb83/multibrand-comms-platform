from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Campaign, OutboundMessage
from app.models.enums import CampaignStatus, MessageStatus
from app.services.common import DomainError, audit, now_utc

TRANSITIONS = {
    "submit": ({CampaignStatus.DRAFT}, CampaignStatus.PENDING_APPROVAL),
    "approve": ({CampaignStatus.PENDING_APPROVAL, CampaignStatus.DRAFT}, CampaignStatus.APPROVED),
    "launch": ({CampaignStatus.APPROVED}, CampaignStatus.RUNNING),
    "pause": ({CampaignStatus.RUNNING, CampaignStatus.APPROVED}, CampaignStatus.PAUSED),
    "resume": ({CampaignStatus.PAUSED}, CampaignStatus.RUNNING),
    "emergency_stop": ({CampaignStatus.RUNNING, CampaignStatus.APPROVED, CampaignStatus.PAUSED,
                        CampaignStatus.PENDING_APPROVAL}, CampaignStatus.EMERGENCY_STOPPED),
    "cancel": ({CampaignStatus.DRAFT, CampaignStatus.PENDING_APPROVAL, CampaignStatus.APPROVED,
                CampaignStatus.PAUSED}, CampaignStatus.CANCELLED),
}


def transition(db: Session, campaign: Campaign, action: str, user_id: int, reason: str | None = None) -> list[int]:
    """Returns ids of messages that became dispatchable (for enqueueing after commit)."""
    if action not in TRANSITIONS:
        raise DomainError(f"unknown action {action}")
    allowed, target = TRANSITIONS[action]
    if campaign.status not in allowed:
        raise DomainError(f"cannot {action} a campaign that is {campaign.status}", 409)
    campaign.status = target
    released: list[int] = []
    if action == "approve":
        campaign.approved_by, campaign.approved_at = user_id, now_utc()
    if action in ("launch", "resume"):
        # drafts become queued only now — never at import time
        db.execute(update(OutboundMessage)
                   .where(OutboundMessage.campaign_id == campaign.id, OutboundMessage.status == MessageStatus.DRAFT)
                   .values(status=MessageStatus.QUEUED, scheduled_at=func.coalesce(OutboundMessage.scheduled_at,
                                                                                   campaign.scheduled_at)))
        released = list(db.scalars(select(OutboundMessage.id).where(OutboundMessage.campaign_id == campaign.id,
                                                                    OutboundMessage.status == MessageStatus.QUEUED)))
    if action in ("emergency_stop", "cancel"):
        campaign.stopped_reason = reason
        db.execute(update(OutboundMessage)
                   .where(OutboundMessage.campaign_id == campaign.id,
                          OutboundMessage.status.in_([MessageStatus.QUEUED, MessageStatus.DRAFT,
                                                      MessageStatus.HELD_FOR_REVIEW]))
                   .values(status=MessageStatus.CANCELLED, hold_reason=f"campaign_{target}"))
    audit(db, f"campaign.{action}", user_id=user_id, brand_id=campaign.brand_id, entity_type="campaign",
          entity_id=campaign.id, reason=reason)
    return released


def stats(db: Session, campaign_id: int) -> dict:
    rows = db.execute(select(OutboundMessage.status, func.count()).where(OutboundMessage.campaign_id == campaign_id)
                      .group_by(OutboundMessage.status)).all()
    return {s: c for s, c in rows}


def emergency_stop_all(db: Session, user_id: int, brand_ids: list[int] | None, reason: str) -> int:
    q = select(Campaign).where(Campaign.status.in_([CampaignStatus.RUNNING, CampaignStatus.APPROVED,
                                                     CampaignStatus.PAUSED]))
    if brand_ids is not None:
        q = q.where(Campaign.brand_id.in_(brand_ids))
    n = 0
    for c in db.scalars(q).all():
        transition(db, c, "emergency_stop", user_id, reason)
        n += 1
    return n
