from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import accessible_brand_ids, brand_role, current_user, require_super_admin
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Brand, User
from app.schemas.api import ChallengeIn, LoginIn, LoginOut, MeOut, MfaStartIn, OtpVerifyIn, SetupIn, TokenOut, UserCreate, UserOut
from app.services import auth_flows, bootstrap
from app.services.common import DomainError, audit, now_utc

router = APIRouter(prefix="/auth", tags=["auth"])
BIND_COOKIE = "g_bind"


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return (name[:2] + "•" * max(1, len(name) - 2)) + "@" + domain


def _finish(db: Session, user: User, method: str) -> LoginOut:
    """Second step if the user opted in, otherwise issue the session token."""
    if user.mfa_email_enabled:
        ch = auth_flows.issue_code(db, user, "login", method)
        db.commit()
        return LoginOut(mfa_required=True, challenge_id=ch.id, email_hint=_mask(user.email))
    user.last_login_at = now_utc()
    audit(db, "auth.login", user_id=user.id, method=method)
    db.commit()
    return LoginOut(access_token=create_access_token(user.id))


@router.get("/providers")
def providers():
    return {"google": auth_flows.google_enabled(), "email_codes_available": auth_flows.mfa_available()}


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalars(select(User).where(User.email == body.email.lower())).first()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return _finish(db, user, "password")


@router.post("/otp/verify", response_model=TokenOut)
def otp_verify(body: OtpVerifyIn, db: Session = Depends(get_db)):
    user = auth_flows.verify_code(db, body.challenge_id, body.code, "login")
    user.last_login_at = now_utc()
    audit(db, "auth.login", user_id=user.id, method="password+email_code")
    db.commit()
    return TokenOut(access_token=create_access_token(user.id))


@router.post("/otp/resend", response_model=LoginOut)
def otp_resend(body: ChallengeIn, db: Session = Depends(get_db)):
    ch = auth_flows.resend_code(db, body.challenge_id)
    user = db.get(User, ch.user_id)
    db.commit()
    return LoginOut(mfa_required=True, challenge_id=ch.id, email_hint=_mask(user.email))


@router.get("/google/start")
def google_start(request: Request):
    url, binding = auth_flows.google_start()
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(BIND_COOKIE, binding, max_age=600, httponly=True, samesite="lax", path="/api/auth/google",
                    secure=get_settings().app_env == "production")
    return resp


@router.get("/google/callback")
def google_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                    g_bind: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    def back(query: str) -> RedirectResponse:
        r = RedirectResponse("/login" + query, status_code=302)
        r.delete_cookie(BIND_COOKIE, path="/api/auth/google")
        return r

    if error or not code or not state:
        return back("?error=" + quote("Google sign-in was cancelled."))
    try:
        user = auth_flows.google_callback(db, code=code, state=state, binding=g_bind)
        out = _finish(db, user, "google")
    except DomainError as exc:
        db.rollback()
        return back("?error=" + quote(str(exc)))
    if out.mfa_required:
        return back(f"?mfa={out.challenge_id}&hint={quote(out.email_hint or '')}")
    return back("#token=" + out.access_token)  # fragment: never sent to any server


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
                 mfa_email_enabled=user.mfa_email_enabled, google_linked=bool(user.google_sub),
                 brands=[{"id": b.id, "slug": b.slug, "name": b.name, "role": brand_role(db, user, b.id),
                          "primary_color": b.primary_color} for b in brands])


# ---- self-service security settings (both directions require a code sent to the account's email)
@router.post("/mfa/email/start", response_model=LoginOut)
def mfa_start(body: MfaStartIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.action == "enable":
        if user.mfa_email_enabled:
            raise HTTPException(409, "Email codes are already on")
        if not auth_flows.mfa_available():
            raise HTTPException(503, "Email delivery isn't configured yet, so codes can't be turned on.")
    elif not user.mfa_email_enabled:
        raise HTTPException(409, "Email codes are already off")
    ch = auth_flows.issue_code(db, user, f"{body.action}_mfa")
    db.commit()
    return LoginOut(mfa_required=True, challenge_id=ch.id, email_hint=_mask(user.email))


@router.post("/mfa/email/confirm", response_model=MeOut)
def mfa_confirm(body: OtpVerifyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.models import AuthChallenge
    ch = db.get(AuthChallenge, body.challenge_id)
    if ch is None or ch.user_id != user.id or ch.purpose not in ("enable_mfa", "disable_mfa"):
        raise HTTPException(400, "Invalid request")
    auth_flows.verify_code(db, body.challenge_id, body.code, ch.purpose)
    user.mfa_email_enabled = ch.purpose == "enable_mfa"
    audit(db, "auth.mfa_email_" + ("enabled" if user.mfa_email_enabled else "disabled"), user_id=user.id)
    db.commit()
    return me(user, db)


@router.post("/google/unlink", response_model=MeOut)
def google_unlink(user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.google_sub = None
    audit(db, "auth.google_unlinked", user_id=user.id)
    db.commit()
    return me(user, db)


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


@users.post("/{user_id}/mfa/reset", response_model=UserOut)
def reset_user_mfa(user_id: int, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Recovery: a Super Admin can turn off email codes for someone who lost mailbox access."""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "user not found")
    u.mfa_email_enabled = False
    audit(db, "auth.mfa_email_reset", user_id=admin.id, entity_type="user", entity_id=u.id)
    db.commit()
    return u
