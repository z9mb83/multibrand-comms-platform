from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import BrandCtx, accessible_brand_ids, brand_ctx, current_user, require_super_admin
from app.core.db import get_db
from app.models import Brand, BrandMembership, MessageTemplate, User
from app.models.enums import Role
from app.schemas.api import BrandIn, BrandOut, BrandUpdate, MembershipIn, TemplateIn, TemplateOut
from app.services.common import audit

router = APIRouter(prefix="/brands", tags=["brands"])


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
    b = Brand(**data, settings={})
    db.add(b)
    db.flush()
    db.add(BrandMembership(user_id=admin.id, brand_id=b.id, role="brand_admin"))
    if master:
        from app.api.routes.mailboxes import add_mailbox
        add_mailbox(db, b, master, "master", admin.id)
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
