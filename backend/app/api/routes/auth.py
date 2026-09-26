from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import accessible_brand_ids, brand_role, current_user, require_super_admin
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Brand, User
from app.schemas.api import (
    ChallengeIn,
    EmailStartIn,
    EmailVerifyIn,
    LoginIn,
    LoginOut,
    MeOut,
    MfaStartIn,
    OtpVerifyIn,
    SetupIn,
    SignupCompleteIn,
    SignupVerifyIn,
    TokenOut,
    UserCreate,
    UserOut,
)
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
def providers(db: Session = Depends(get_db)):
    return {"google": auth_flows.google_enabled(), "email_codes_available": auth_flows.mfa_available(db),
            "needs_owner": bootstrap.needs_setup(db), "open_signup": True}


# ------------------------------------------------------------------ sign up: email -> code -> password
@router.post("/signup/start", response_model=LoginOut)
def signup_start(body: EmailStartIn, db: Session = Depends(get_db)):
    """Step 1: email a 6-digit code to prove the address. The very first account becomes the Super Admin."""
    email = body.email.lower()
    if bootstrap.needs_setup(db):
        ch = auth_flows.issue_code(db, None, "signup_owner", "email", email=email)
    else:
        existing = db.scalars(select(User).where(func.lower(User.email) == email)).first()
        if existing is not None:
            raise HTTPException(409, "An account with this email already exists. Use the Sign in tab."
                                if existing.is_active else "This account was removed. Ask your Super Admin to add you again.")
        ch = auth_flows.issue_code(db, None, "signup", "email", email=email)
    db.commit()
    return LoginOut(mfa_required=True, challenge_id=ch.id, email_hint=_mask(email))


@router.post("/signup/verify")
def signup_verify(body: SignupVerifyIn, db: Session = Depends(get_db)):
    """Step 2: check the code. Returns a short-lived token that allows choosing a password."""
    ch = auth_flows.check_code(db, body.challenge_id, body.code, ("signup", "signup_owner"))
    db.commit()
    return {"signup_token": auth_flows.signup_token(ch.email, ch.purpose == "signup_owner"), "email": ch.email,
            "owner": ch.purpose == "signup_owner"}


@router.post("/signup/complete", response_model=TokenOut, status_code=201)
def signup_complete(body: SignupCompleteIn, db: Session = Depends(get_db)):
    """Step 3: create the account with a password. New accounts have no brand access until the Super Admin grants it."""
    email, owner = auth_flows.read_signup_token(body.signup_token)
    if owner:
        user = bootstrap.create_owner(db, email=email, full_name=body.full_name.strip(), password=body.password,
                                      via="email_code")
    else:
        db.execute(text("SELECT pg_advisory_xact_lock(4242003)"))
        if db.scalar(select(User.id).where(func.lower(User.email) == email)):
            raise HTTPException(409, "An account with this email already exists. Use the Sign in tab.")
        user = User(email=email, full_name=body.full_name.strip(), password_hash=hash_password(body.password),
                    is_super_admin=False)
        db.add(user)
        db.flush()
        audit(db, "auth.signed_up", user_id=user.id, via="email_code")
    user.last_login_at = now_utc()
    db.commit()
    return TokenOut(access_token=create_access_token(user.id))


@router.get("/code-sender")
def get_code_sender(_: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    from app.models import AppSettings, SenderAccount
    from app.services.mailer import code_sender
    row = db.get(AppSettings, 1)
    options = db.scalars(select(SenderAccount).where(SenderAccount.channel == "email", SenderAccount.provider == "gmail",
                                                     SenderAccount.connection_status == "connected")
                         .order_by(SenderAccount.address)).all()
    acc = code_sender(db)
    s = get_settings()
    return {"selected_id": row.code_sender_id if row else None, "working": acc is not None or s.mailer in ("smtp", "resend"),
            "env_mailer": s.mailer if s.mailer in ("smtp", "resend") else None,
            "options": [{"id": o.id, "address": o.address} for o in options]}


@router.put("/code-sender")
def put_code_sender(body: dict, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    from app.models import AppSettings, SenderAccount
    sid = body.get("sender_id")
    if sid is not None:
        acc = db.get(SenderAccount, int(sid))
        if acc is None or acc.provider != "gmail" or acc.connection_status != "connected":
            raise HTTPException(400, "Pick a mailbox that is connected with Google")
    row = db.get(AppSettings, 1)
    if row is None:
        row = AppSettings(id=1)
        db.add(row)
    row.code_sender_id = int(sid) if sid is not None else None
    audit(db, "auth.code_sender_set", user_id=admin.id, sender_id=sid)
    db.commit()
    return get_code_sender(admin, db)


@router.post("/email/start", response_model=LoginOut)
def email_start(body: EmailStartIn, db: Session = Depends(get_db)):
    """Passwordless: email a sign-in code. On a new installation this creates the owner account instead.
    Unknown addresses get the same response (no code is sent) so accounts can't be discovered."""
    email = body.email.lower()
    if bootstrap.needs_setup(db):
        ch = auth_flows.issue_code(db, None, "signup_owner", "email", email=email)
    else:
        user = db.scalars(select(User).where(User.email == email)).first()
        if user is None or not user.is_active:
            raise HTTPException(404, f"There is no account for {email} yet. Open the Sign up tab to create one.")
        ch = auth_flows.issue_code(db, user, "email_login", "email")
    db.commit()
    return LoginOut(mfa_required=True, challenge_id=ch.id, email_hint=_mask(email))


@router.post("/email/verify", response_model=TokenOut)
def email_verify(body: EmailVerifyIn, db: Session = Depends(get_db)):
    ch = auth_flows.check_code(db, body.challenge_id, body.code, ("email_login", "signup_owner"))
    if ch.purpose == "signup_owner":
        user = bootstrap.create_owner(db, email=ch.email, full_name=(body.full_name or ch.email.split("@")[0]).strip(),
                                      via="email_code")
    else:
        user = db.get(User, ch.user_id)
        if user is None or not user.is_active:
            raise HTTPException(401, "Account disabled")
    user.last_login_at = now_utc()
    audit(db, "auth.login", user_id=user.id, method="email_code")
    db.commit()
    return TokenOut(access_token=create_access_token(user.id))


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
def google_start(request: Request, mode: str = "signin"):
    url, binding = auth_flows.google_start(mode="signup" if mode == "signup" else "signin")
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
    kind = "login"
    try:
        kind = auth_flows._unsign(state).get("k", "login")
    except DomainError:
        pass
    if kind == "mailbox":
        page = "/brands"
        try:
            page = "/accounts/email" if auth_flows._unsign(state).get("m") == "accounts" else "/brands"
        except DomainError:
            pass
        try:
            acc = auth_flows.connect_mailbox(db, code=code, state=state, binding=g_bind)
            db.commit()
            r = RedirectResponse(f"{page}?brand={acc.brand_id}&connected={quote(acc.address)}", status_code=302)
        except DomainError as exc:
            db.rollback()
            r = RedirectResponse(f"{page}?error=" + quote(str(exc)), status_code=302)
        r.delete_cookie(BIND_COOKIE, path="/api/auth/google")
        return r
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
    import secrets as _secrets

    from app.models import BrandMembership
    from app.models.enums import Role
    email = body.email.lower()
    existing = db.scalars(select(User).where(User.email == email)).first()
    if existing is not None and existing.is_active:
        raise HTTPException(409, "email already exists")
    if body.brand_id is not None:
        if db.get(Brand, body.brand_id) is None:
            raise HTTPException(404, "brand not found")
        if body.role not in {r.value for r in Role}:
            raise HTTPException(400, "invalid role")
    if existing is not None:  # re-inviting someone who was removed
        u = existing
        u.is_active, u.full_name, u.is_super_admin = True, body.full_name, body.is_super_admin
    else:
        u = User(email=email, full_name=body.full_name, is_super_admin=body.is_super_admin, phone=body.phone,
                 password_hash=hash_password(body.password or _secrets.token_urlsafe(32)))
        db.add(u)
    db.flush()
    if body.brand_id is not None:
        m = db.scalars(select(BrandMembership).where(BrandMembership.brand_id == body.brand_id,
                                                     BrandMembership.user_id == u.id)).first()
        if m is None:
            db.add(BrandMembership(user_id=u.id, brand_id=body.brand_id, role=body.role))
        else:
            m.role = body.role
    audit(db, "user.invited", user_id=admin.id, entity_type="user", entity_id=u.id, super_admin=body.is_super_admin)
    db.commit()
    return u


@users.get("/team")
def team(admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    from app.models import BrandMembership
    rows = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)).all()
    names = {b.id: b.name for b in db.scalars(select(Brand)).all()}
    mems: dict[int, list] = {}
    for m in db.scalars(select(BrandMembership)).all():
        mems.setdefault(m.user_id, []).append({"brand_id": m.brand_id, "brand": names.get(m.brand_id), "role": m.role})
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "is_super_admin": u.is_super_admin,
             "google_linked": bool(u.google_sub), "is_you": u.id == admin.id, "brands": mems.get(u.id, [])}
            for u in rows]


@users.put("/{user_id}/access", response_model=UserOut)
def set_access(user_id: int, body: dict, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Give an existing account access: Super Admin, or a role on one brand."""
    from app.models import BrandMembership
    from app.models.enums import Role
    u = db.get(User, user_id)
    if u is None or not u.is_active:
        raise HTTPException(404, "user not found")
    if body.get("is_super_admin") is not None:
        if not body["is_super_admin"] and u.id == admin.id:
            raise HTTPException(400, "You can't remove your own Super Admin access")
        u.is_super_admin = bool(body["is_super_admin"])
    if body.get("brand_id") is not None:
        if db.get(Brand, int(body["brand_id"])) is None:
            raise HTTPException(404, "brand not found")
        role = body.get("role") or "viewer"
        if role not in {r.value for r in Role}:
            raise HTTPException(400, "invalid role")
        m = db.scalars(select(BrandMembership).where(BrandMembership.brand_id == int(body["brand_id"]),
                                                     BrandMembership.user_id == u.id)).first()
        if m is None:
            db.add(BrandMembership(user_id=u.id, brand_id=int(body["brand_id"]), role=role))
        else:
            m.role = role
    audit(db, "user.access_set", user_id=admin.id, entity_type="user", entity_id=u.id, **{k: str(v) for k, v in body.items()})
    db.commit()
    return u


@users.delete("/{user_id}", status_code=204)
def remove_user(user_id: int, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None or not u.is_active:
        raise HTTPException(404, "user not found")
    if u.id == admin.id:
        raise HTTPException(400, "You can't remove yourself")
    if u.is_super_admin and db.scalar(select(func.count()).select_from(User).where(
            User.is_super_admin.is_(True), User.is_active.is_(True))) <= 1:
        raise HTTPException(400, "Keep at least one Super Admin")
    u.is_active = False
    audit(db, "user.removed", user_id=admin.id, entity_type="user", entity_id=u.id)
    db.commit()


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
