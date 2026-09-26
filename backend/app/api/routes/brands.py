from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import BrandCtx, accessible_brand_ids, brand_ctx, current_user, require_super_admin
from app.core.db import get_db
from app.models import Brand, BrandMembership, MessageTemplate, User
from app.models.enums import Role
from app.schemas.api import (
    BrandDeleteIn,
    BrandIn,
    BrandOut,
    BrandProfileIn,
    BrandUpdate,
    MembershipIn,
    TemplateIn,
    TemplateOut,
)
from app.services.common import audit

router = APIRouter(prefix="/brands", tags=["brands"])


SAMPLE_CONFIRM = "remove sample brands"


@router.get("/samples")
def sample_brands(_: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """The example brands created at first setup (same slugs as bootstrap.BRANDS) that still exist."""
    from app.models import SenderAccount
    from app.services.bootstrap import BRANDS
    slugs = [slug for slug, _, _ in BRANDS]
    out = []
    for b in db.scalars(select(Brand).where(Brand.slug.in_(slugs)).order_by(Brand.name)):
        demo = db.scalar(select(func.count()).select_from(SenderAccount).where(
            SenderAccount.brand_id == b.id, SenderAccount.provider == "mock", SenderAccount.address.like("%.example.com")))
        out.append({"id": b.id, "name": b.name, "has_demo_mailboxes": bool(demo)})
    return {"brands": out, "confirm_text": SAMPLE_CONFIRM}


@router.post("/samples/remove")
def remove_sample_brands(body: dict, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Delete all example brands from first setup, with everything in them. Requires typing the confirmation text."""
    from sqlalchemy import delete, update

    from app.models import AuditLog
    from app.services.bootstrap import BRANDS
    if str(body.get("confirm", "")).strip().lower() != SAMPLE_CONFIRM:
        raise HTTPException(400, f'Type "{SAMPLE_CONFIRM}" to confirm')
    slugs = [slug for slug, _, _ in BRANDS]
    ids = list(db.scalars(select(Brand.id).where(Brand.slug.in_(slugs))))
    names = list(db.scalars(select(Brand.name).where(Brand.id.in_(ids or [0]))))
    if ids:
        db.execute(update(AuditLog).where(AuditLog.brand_id.in_(ids)).values(brand_id=None))
        db.execute(delete(Brand).where(Brand.id.in_(ids)))
    db.expunge_all()
    audit(db, "brand.samples_removed", user_id=admin.id, names=names)
    db.commit()
    return {"deleted": names}


@router.get("", response_model=list[BrandOut])
def list_brands(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = accessible_brand_ids(db, user)
    return db.scalars(select(Brand).where(Brand.id.in_(ids)).order_by(Brand.name)).all() if ids else []


@router.post("", response_model=BrandOut, status_code=201)
def create_brand(body: BrandIn, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    if db.scalar(select(Brand.id).where(Brand.slug == body.slug)):
        raise HTTPException(409, "slug already exists")
    data = body.model_dump()
    master = data.pop("master_email", None)
    profile = {k: data.pop(k) for k in ("description", "phone", "website", "signature_name", "signature_title")}
    if db.scalar(select(Brand.id).where(func.lower(Brand.name) == data["name"].strip().lower())):
        raise HTTPException(409, "A brand with this name already exists")
    b = Brand(**data, settings={})
    db.add(b)
    db.flush()
    db.add(BrandMembership(user_id=admin.id, brand_id=b.id, role="brand_admin"))
    if master:
        from app.api.routes.mailboxes import add_mailbox
        add_mailbox(db, b, master, "master", admin.id)
        b.master_email = master.lower()
    from app.services.outreach.engine import settings_for
    st = settings_for(db, b.id)
    st.cc_email = (master or "").lower()
    st.brand_facts = (profile["description"] or "").strip()
    st.phone = (profile["phone"] or "").strip()
    st.website = (profile["website"] or "").strip()
    if profile["signature_name"] is not None:
        st.signature_name = profile["signature_name"].strip()
    if profile["signature_title"] is not None:
        st.signature_title = profile["signature_title"].strip()
    audit(db, "brand.created", user_id=admin.id, brand_id=b.id, entity_type="brand", entity_id=b.id)
    db.commit()
    return b


@router.get("/{brand_id}", response_model=BrandOut)
def get_brand(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    return db.get(Brand, ctx.brand_id)


@router.patch("/{brand_id}", response_model=BrandOut)
def update_brand(body: BrandUpdate, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    b = db.get(Brand, ctx.brand_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(b, k, v)
    audit(db, "brand.updated", user_id=ctx.user.id, brand_id=b.id, entity_type="brand", entity_id=b.id)
    db.commit()
    return b


def profile_out(db: Session, b: Brand) -> dict:
    from app.services.outreach.engine import settings_for
    st = settings_for(db, b.id)
    return {"id": b.id, "name": b.name, "slug": b.slug, "primary_color": b.primary_color, "timezone": b.timezone,
            "master_email": b.master_email, "description": st.brand_facts, "phone": st.phone, "website": st.website,
            "cc_email": st.cc_email, "signature_name": st.signature_name, "signature_title": st.signature_title}


@router.get("/{brand_id}/profile")
def get_profile(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    out = profile_out(db, db.get(Brand, ctx.brand_id))
    db.commit()
    return out


@router.put("/{brand_id}/profile")
def put_profile(body: BrandProfileIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    from app.services.outreach.engine import is_valid_email, settings_for
    ctx.require(Role.BRAND_ADMIN)
    b = db.get(Brand, ctx.brand_id)
    st = settings_for(db, b.id)
    data = body.model_dump(exclude_unset=True)
    if data.get("name"):
        name = data["name"].strip()
        if db.scalar(select(Brand.id).where(func.lower(Brand.name) == name.lower(), Brand.id != b.id)):
            raise HTTPException(409, "A brand with this name already exists")
        b.name = name
    for k in ("primary_color", "timezone"):
        if data.get(k):
            setattr(b, k, data[k])
    if "cc_email" in data:
        cc = (data["cc_email"] or "").strip().lower()
        if cc and not is_valid_email(cc):
            raise HTTPException(400, "CC email is not a valid address")
        st.cc_email = cc
    mapping = {"description": "brand_facts", "phone": "phone", "website": "website", "signature_name": "signature_name",
               "signature_title": "signature_title"}
    for k, col in mapping.items():
        if k in data:
            setattr(st, col, (data[k] or "").strip())
    audit(db, "brand.profile_updated", user_id=ctx.user.id, brand_id=b.id, fields=sorted(data))
    db.commit()
    return profile_out(db, b)


@router.post("/{brand_id}/delete")
def delete_brand(brand_id: int, body: BrandDeleteIn, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Permanently delete a brand and everything in it (mailboxes, leads, contacts, campaigns, messages).
    The exact brand name must be typed to confirm."""
    from sqlalchemy import delete, update

    from app.models import AuditLog
    b = db.get(Brand, brand_id)
    if b is None:
        raise HTTPException(404, "brand not found")
    if body.confirm_name.strip() != b.name:
        raise HTTPException(400, "Type the brand name exactly to confirm")
    name = b.name
    db.execute(update(AuditLog).where(AuditLog.brand_id == b.id).values(brand_id=None))
    db.execute(delete(Brand).where(Brand.id == brand_id))  # the database cascades to everything in the brand
    db.expunge_all()
    audit(db, "brand.deleted", user_id=admin.id, entity_type="brand", entity_id=brand_id, name=name)
    db.commit()
    return {"deleted": name}


@router.get("/{brand_id}/members")
def list_members(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    rows = db.execute(select(BrandMembership, User).join(User, User.id == BrandMembership.user_id)
                      .where(BrandMembership.brand_id == ctx.brand_id)).all()
    return [{"user_id": u.id, "email": u.email, "full_name": u.full_name, "role": m.role} for m, u in rows]


@router.put("/{brand_id}/members")
def set_member(body: MembershipIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    if body.role not in {r.value for r in Role}:
        raise HTTPException(400, "invalid role")
    m = db.scalars(select(BrandMembership).where(BrandMembership.brand_id == ctx.brand_id,
                                                 BrandMembership.user_id == body.user_id)).first()
    if m is None:
        m = BrandMembership(brand_id=ctx.brand_id, user_id=body.user_id)
        db.add(m)
    m.role = body.role
    audit(db, "membership.set", user_id=ctx.user.id, brand_id=ctx.brand_id, entity_type="user",
          entity_id=body.user_id, role=body.role)
    db.commit()
    return {"ok": True}


@router.get("/{brand_id}/templates", response_model=list[TemplateOut])
def list_templates(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    return db.scalars(select(MessageTemplate).where(MessageTemplate.brand_id == ctx.brand_id)
                      .order_by(MessageTemplate.name)).all()


@router.post("/{brand_id}/templates", response_model=TemplateOut, status_code=201)
def create_template(body: TemplateIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.MANAGER)
    t = MessageTemplate(brand_id=ctx.brand_id, variables=[], **body.model_dump())
    db.add(t)
    db.commit()
    return t
