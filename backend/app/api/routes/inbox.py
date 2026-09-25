from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import accessible_brand_ids, current_user
from app.core.db import get_db
from app.models import Brand, BrandContact, Conversation, InboundMessage, OutboundMessage, SenderAccount, User
from app.models.enums import MessageStatus, RoutingMode
from app.schemas.api import ClassifyIn, ConversationOut, ConversationUpdate, InboundOut, ReplyIn
from app.services import followups
from app.services.common import audit
from app.services.dispatch import create_message
from app.services.inbound import set_classification
from app.services.queue import enqueue_dispatch

router = APIRouter(prefix="/inbox", tags=["inbox"])


def _conv(db: Session, user: User, cid: int) -> Conversation:
    c = db.get(Conversation, cid)
    if c is None or c.brand_id not in accessible_brand_ids(db, user):
        raise HTTPException(404, "conversation not found")
    return c


@router.get("")
def list_conversations(brand_id: int | None = None, status: str | None = None, channel: str | None = None,
                       limit: int = Query(50, le=200), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Unified inbox across all brands the user can access (or one brand)."""
    ids = accessible_brand_ids(db, user)
    if brand_id is not None:
        if brand_id not in ids:
            raise HTTPException(403, "No access to this brand")
        ids = {brand_id}
    if not ids:
        return []
    q = (select(Conversation, BrandContact, Brand.name, SenderAccount.address)
         .join(BrandContact, (BrandContact.contact_id == Conversation.contact_id) & (BrandContact.brand_id == Conversation.brand_id))
         .join(Brand, Brand.id == Conversation.brand_id)
         .outerjoin(SenderAccount, SenderAccount.id == Conversation.sender_account_id)
         .where(Conversation.brand_id.in_(ids), Conversation.last_inbound_at.is_not(None)))
    if status:
        q = q.where(Conversation.status == status)
    if channel:
        q = q.where(Conversation.channel == channel)
    out = []
    for conv, bc, brand_name, sender in db.execute(q.order_by(Conversation.last_inbound_at.desc()).limit(limit)):
        last = db.scalars(select(InboundMessage).where(InboundMessage.conversation_id == conv.id)
                          .order_by(InboundMessage.received_at.desc()).limit(1)).first()
        out.append({**ConversationOut.model_validate(conv).model_dump(), "brand_name": brand_name,
                    "sender_address": sender, "contact_name": bc.contact_name, "contact_email": bc.email,
                    "contact_phone": bc.phone, "last_message": last.body[:200] if last and last.body else None,
                    "classification": last.classification if last else None})
    return out


@router.get("/{conversation_id}")
def conversation_detail(conversation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _conv(db, user, conversation_id)
    inbound = db.scalars(select(InboundMessage).where(InboundMessage.conversation_id == c.id)).all()
    outbound = db.scalars(select(OutboundMessage).where(OutboundMessage.conversation_id == c.id)).all()
    timeline = ([{"direction": "in", "at": m.received_at, **InboundOut.model_validate(m).model_dump()} for m in inbound] +
                [{"direction": "out", "at": m.sent_at or m.created_at, "id": m.id, "body": m.body, "subject": m.subject,
                  "status": m.status, "sender_account_id": m.sender_account_id} for m in outbound])
    timeline.sort(key=lambda x: x["at"])
    c.unread_count = 0
    db.commit()
    return {"conversation": ConversationOut.model_validate(c), "timeline": timeline}


@router.patch("/{conversation_id}", response_model=ConversationOut)
def update_conversation(conversation_id: int, body: ConversationUpdate, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    c = _conv(db, user, conversation_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    if body.status == "closed":
        followups.cancel_for_contact(db, c.brand_id, c.contact_id, reason="conversation_closed")
    audit(db, "conversation.updated", user_id=user.id, brand_id=c.brand_id, entity_type="conversation", entity_id=c.id)
    db.commit()
    return c


@router.post("/{conversation_id}/reply")
def reply(conversation_id: int, body: ReplyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Replies always go from the conversation's original sender (thread continuity)."""
    c = _conv(db, user, conversation_id)
    bc = db.scalars(select(BrandContact).where(BrandContact.brand_id == c.brand_id,
                                               BrandContact.contact_id == c.contact_id)).one()
    msg = create_message(db, brand_id=c.brand_id, contact_id=c.contact_id, channel=c.channel,
                         to_address=bc.email if c.channel == "email" else bc.phone, subject=body.subject,
                         body=body.body, routing_mode=RoutingMode.STICKY, status=MessageStatus.QUEUED,
                         conversation_id=c.id)
    audit(db, "conversation.reply", user_id=user.id, brand_id=c.brand_id, entity_type="conversation", entity_id=c.id)
    db.commit()
    enqueue_dispatch([msg.id])
    db.expire_all()
    m = db.get(OutboundMessage, msg.id)
    return {"message_id": m.id, "status": m.status, "hold_reason": m.hold_reason}


@router.post("/{conversation_id}/resume-followups")
def resume_followups(conversation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _conv(db, user, conversation_id)
    n = followups.resume(db, c.id, user.id)
    db.commit()
    return {"resumed": n}


@router.post("/messages/{inbound_id}/classification", response_model=InboundOut)
def classify(inbound_id: int, body: ClassifyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = db.get(InboundMessage, inbound_id)
    if m is None or m.brand_id not in accessible_brand_ids(db, user):
        raise HTTPException(404, "message not found")
    set_classification(db, inbound_id, body.classification, user.id)
    db.commit()
    return m
