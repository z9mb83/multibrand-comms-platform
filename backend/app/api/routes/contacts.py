from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import BrandCtx, brand_ctx
from app.core.db import get_db
from app.models import BrandContact, ContactConsent
from app.models.enums import Role
from app.schemas.api import ConsentIn, ContactIn, ContactOut, UnsubscribeIn
from app.services.common import normalize_email, normalize_phone
from app.services.contacts import set_consent, unsubscribe, upsert_brand_contact

router = APIRouter(prefix="/brands/{brand_id}/contacts", tags=["contacts"])


def _get(db: Session, brand_id: int, contact_id: int) -> BrandContact:
    bc = db.scalars(select(BrandContact).where(BrandContact.brand_id == brand_id,
                                               BrandContact.contact_id == contact_id)).first()
    if bc is None:
        raise HTTPException(404, "contact not found in this brand")
    return bc


@router.get("")
def list_contacts(q: str | None = None, limit: int = Query(50, le=500), offset: int = 0,
                  ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    stmt = select(BrandContact).where(BrandContact.brand_id == ctx.brand_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(BrandContact.contact_name).like(like), BrandContact.email.like(like),
                              BrandContact.phone.like(like), func.lower(BrandContact.company_name).like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(BrandContact.id.desc()).limit(limit).offset(offset)).all()
    consents = {}
    if rows:
        for c in db.scalars(select(ContactConsent).where(ContactConsent.brand_id == ctx.brand_id,
                                                         ContactConsent.contact_id.in_([r.contact_id for r in rows]))):
            consents.setdefault(c.contact_id, {})[c.channel] = c.status
    return {"total": total, "items": [{**ContactOut.model_validate(r).model_dump(),
                                       "consent": consents.get(r.contact_id, {})} for r in rows]}


@router.post("", response_model=ContactOut, status_code=201)
def create_contact(body: ContactIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.AGENT)
    email, phone = normalize_email(body.email), normalize_phone(body.phone)
    if not email and not phone:
        raise HTTPException(400, "valid email or phone required")
    bc = upsert_brand_contact(db, ctx.brand_id, email=email, phone=phone, contact_name=body.contact_name,
                              company_name=body.company_name, notes=body.notes)
    for ch, granted in body.consent.items():
        set_consent(db, ctx.brand_id, bc.contact_id, ch, granted, source=body.consent_source or "manual",
                    user_id=ctx.user.id)
    db.commit()
    return bc


@router.post("/{contact_id}/consent")
def update_consent(contact_id: int, body: ConsentIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.AGENT)
    _get(db, ctx.brand_id, contact_id)
    c = set_consent(db, ctx.brand_id, contact_id, body.channel, body.granted, body.source or "manual", ctx.user.id)
    db.commit()
    return {"channel": c.channel, "status": c.status}


@router.post("/{contact_id}/unsubscribe")
def unsubscribe_contact(contact_id: int, body: UnsubscribeIn, ctx: BrandCtx = Depends(brand_ctx),
                        db: Session = Depends(get_db)):
    ctx.require(Role.AGENT)
    bc = _get(db, ctx.brand_id, contact_id)
    addr = None
    if body.channel:
        addr = bc.email if body.channel == "email" else bc.phone
    unsubscribe(db, ctx.brand_id, contact_id, body.channel, addr, reason="manual", user_id=ctx.user.id)
    db.commit()
    return {"ok": True}
