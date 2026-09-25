from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import accessible_brand_ids, brand_role, current_user, require_super_admin
from app.core.db import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Brand, User
from app.schemas.api import LoginIn, MeOut, SetupIn, TokenOut, UserCreate, UserOut
from app.services import bootstrap
from app.services.common import audit, now_utc

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalars(select(User).where(User.email == body.email.lower())).first()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    user.last_login_at = now_utc()
    audit(db, "auth.login", user_id=user.id)
    db.commit()
    return TokenOut(access_token=create_access_token(user.id))


@router.get("/setup-status")
def setup_status(db: Session = Depends(get_db)):
    return {"needs_setup": bootstrap.needs_setup(db)}


@router.post("/setup", response_model=TokenOut, status_code=201)
def setup(body: SetupIn, db: Session = Depends(get_db)):
    """First-run only: creates the Super Admin chosen by the operator. Disabled once any user exists."""
    admin = bootstrap.create_first_admin(db, email=body.email, full_name=body.full_name, password=body.password,
                                         with_brands=body.create_brands)
    db.commit()
    return TokenOut(access_token=create_access_token(admin.id))


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = accessible_brand_ids(db, user)
    brands = db.scalars(select(Brand).where(Brand.id.in_(ids)).order_by(Brand.name)).all() if ids else []
    return MeOut(id=user.id, email=user.email, full_name=user.full_name, is_super_admin=user.is_super_admin,
                 brands=[{"id": b.id, "slug": b.slug, "name": b.name, "role": brand_role(db, user, b.id),
                          "primary_color": b.primary_color} for b in brands])


users = APIRouter(prefix="/users", tags=["users"])


@users.get("", response_model=list[UserOut])
def list_users(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)).all()


@users.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    if db.scalar(select(User.id).where(User.email == body.email.lower())):
        raise HTTPException(409, "email already exists")
    u = User(email=body.email.lower(), full_name=body.full_name, password_hash=hash_password(body.password),
             is_super_admin=body.is_super_admin, phone=body.phone)
    db.add(u)
    db.flush()
    audit(db, "user.created", user_id=admin.id, entity_type="user", entity_id=u.id)
    db.commit()
    return u
