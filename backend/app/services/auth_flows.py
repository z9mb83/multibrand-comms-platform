"""Sign-in flows: email one-time codes (optional per user) and Sign in with Google (OpenID Connect).

Security notes
- OTP: 6 digits from `secrets`, stored as HMAC-SHA256(server secret, challenge id + code); 10-minute expiry,
  5 attempts, single use, constant-time compare; at most 5 codes per user per 15 minutes, 30s resend gap.
- Google: authorization-code flow. `state` is a short-lived HMAC-signed token bound to a browser cookie (login-CSRF
  protection) and carries the OIDC nonce. The ID token is verified against Google's JWKS (signature, audience,
  issuer, expiry, nonce) and the email must be verified. Google sign-in only works for users that already exist;
  it never creates accounts.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import timedelta
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import AuthChallenge, User
from app.services.common import DomainError, audit, now_utc
from app.services.mailer import MailerError, can_deliver, send_email

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
STATE_TTL = 600


# ----------------------------------------------------------------------------- email OTP
def _hash_code(challenge_id: str, code: str) -> str:
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, f"{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


PURPOSE_TEXT = {
    "login": ("sign in to your account", "If you didn't try to sign in, change your password and tell your administrator."),
    "email_login": ("sign in to your account", "If you didn't try to sign in, you can ignore this email."),
    "signup_owner": ("create the owner account", "If you didn't request this, you can ignore this email."),
    "signup": ("create your Comms Hub account", "If you didn't request this, you can ignore this email."),
    "enable_mfa": ("turn on email codes for your account", "If this wasn't you, tell your administrator."),
    "disable_mfa": ("turn off email codes for your account", "If this wasn't you, tell your administrator."),
    "verify_mailbox": ("confirm this mailbox for sending from Comms Hub",
                       "If you don't recognise this request, you can ignore this email."),
}


def issue_code(db: Session, user: User | None, purpose: str, method: str = "password", *, email: str | None = None,
               sender_account_id: int | None = None) -> AuthChallenge:
    """Create a one-time code and email it. The code goes to `email` if given, otherwise to the user's email."""
    s = get_settings()
    target = (email or (user.email if user else "")).lower()
    if not target:
        raise DomainError("No email address to send the code to")
    now = now_utc()
    recent = db.scalars(select(AuthChallenge).where(AuthChallenge.email == target,
                                                    AuthChallenge.created_at > now - timedelta(minutes=15))
                        .order_by(AuthChallenge.created_at.desc())).all()
    if len(recent) >= 5:
        raise DomainError("Too many codes requested. Try again in 15 minutes.", 429)
    if recent and (now - recent[0].created_at).total_seconds() < 30:
        raise DomainError("Please wait 30 seconds before requesting another code.", 429)
    code = f"{secrets.randbelow(1_000_000):06d}"
    ch = AuthChallenge(id=str(uuid.uuid4()), user_id=user.id if user else None, email=target, purpose=purpose,
                       method=method, sender_account_id=sender_account_id, attempts=0,
                       expires_at=now + timedelta(minutes=s.otp_ttl_minutes), created_at=now)
    ch.code_hash = _hash_code(ch.id, code)
    db.add(ch)
    db.flush()
    what, footer = PURPOSE_TEXT.get(purpose, ("continue", ""))
    text = f"Your Comms Hub code is {code}\n\nUse it to {what}. It expires in {s.otp_ttl_minutes} minutes.\n{footer}"
    html = (f"<p>Your Comms Hub code is</p><p style='font-size:28px;font-weight:700;letter-spacing:6px'>{code}</p>"
            f"<p>Use it to {what}. It expires in {s.otp_ttl_minutes} minutes.</p><p style='color:#666'>{footer}</p>")
    try:
        send_email(target, f"{code} is your Comms Hub code", text, html, db=db)
    except MailerError as exc:
        raise DomainError(f"Could not send the code: {exc}", 503) from exc
    audit(db, "auth.otp_sent", user_id=user.id if user else None, purpose=purpose)
    return ch


def check_code(db: Session, challenge_id: str, code: str, purposes: tuple[str, ...]) -> AuthChallenge:
    """Validate and consume a code; returns the challenge."""
    s = get_settings()
    ch = db.scalars(select(AuthChallenge).where(AuthChallenge.id == challenge_id).with_for_update()).first()
    if ch is None or ch.purpose not in purposes or ch.consumed_at is not None or ch.expires_at < now_utc():
        raise DomainError("This code has expired. Request a new one.", 401)
    if ch.attempts >= s.otp_max_attempts:
        raise DomainError("Too many wrong attempts. Request a new code.", 429)
    ch.attempts += 1
    ok = hmac.compare_digest(ch.code_hash, _hash_code(ch.id, (code or "").strip()))
    if not ok:
        db.commit()  # persist the attempt counter
        left = s.otp_max_attempts - ch.attempts
        raise DomainError(f"Incorrect code. {left} attempt{'s' if left != 1 else ''} left." if left else
                          "Too many wrong attempts. Request a new code.", 401)
    ch.consumed_at = now_utc()
    return ch


def verify_code(db: Session, challenge_id: str, code: str, purpose: str) -> User:
    ch = check_code(db, challenge_id, code, (purpose,))
    user = db.get(User, ch.user_id) if ch.user_id else None
    if user is None or not user.is_active:
        raise DomainError("Account disabled", 401)
    return user


def resend_code(db: Session, challenge_id: str) -> AuthChallenge:
    ch = db.get(AuthChallenge, challenge_id)
    if ch is None or ch.consumed_at is not None:
        raise DomainError("Start again.", 401)
    user = db.get(User, ch.user_id) if ch.user_id else None
    ch.consumed_at = now_utc()  # the old code stops working
    return issue_code(db, user, ch.purpose, ch.method, email=ch.email, sender_account_id=ch.sender_account_id)


def mfa_available(db=None) -> bool:
    return can_deliver(db)


SIGNUP_TOKEN_TTL = 20 * 60


def signup_token(email: str, owner: bool) -> str:
    """Proof that this browser just verified `email` with a code; exchanged for the account once a password is chosen."""
    return jwt.encode({"e": email, "o": owner, "p": "signup", "exp": int(time.time()) + SIGNUP_TOKEN_TTL},
                      get_settings().jwt_secret, algorithm="HS256")


def read_signup_token(token: str) -> tuple[str, bool]:
    try:
        data = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise DomainError("Your verification expired. Start again.", 401) from exc
    if data.get("p") != "signup":
        raise DomainError("Your verification expired. Start again.", 401)
    return data["e"], bool(data.get("o"))


# ----------------------------------------------------------------------------- Google
def google_enabled() -> bool:
    s = get_settings()
    return bool(s.google_client_id and s.google_client_secret)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _sign(payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(get_settings().jwt_secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _unsign(token: str) -> dict:
    try:
        body, sig = token.split(".", 1)
    except ValueError as exc:
        raise DomainError("Invalid sign-in state", 400) from exc
    expected = _b64(hmac.new(get_settings().jwt_secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise DomainError("Invalid sign-in state", 400)
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if data.get("exp", 0) < time.time():
        raise DomainError("Sign-in took too long. Please try again.", 400)
    return data


def redirect_uri() -> str:
    return get_settings().public_app_url.rstrip("/") + "/api/auth/google/callback"


GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]


def google_start(kind: str = "login", *, user_id: int | None = None, sender_account_id: int | None = None,
                 login_hint: str | None = None, mode: str = "signin") -> tuple[str, str]:
    """Returns (authorization URL, browser-binding value for an HttpOnly cookie).
    kind="login": sign in / owner sign-up.  kind="mailbox": connect a sending mailbox (Gmail API access)."""
    if not google_enabled():
        raise DomainError("Google is not configured on the server yet", 404)
    binding = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(16)
    state = _sign({"b": hashlib.sha256(binding.encode()).hexdigest(), "n": nonce, "k": kind, "u": user_id,
                   "s": sender_account_id, "m": mode, "exp": int(time.time()) + STATE_TTL})
    params = {"client_id": get_settings().google_client_id, "redirect_uri": redirect_uri(), "response_type": "code",
              "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account"}
    if kind == "mailbox":
        params.update(scope="openid email " + " ".join(GMAIL_SCOPES), access_type="offline",
                      prompt="consent select_account", include_granted_scopes="true")
    if login_hint:
        params["login_hint"] = login_hint
    return f"{GOOGLE_AUTH}?{urlencode(params)}", binding


_jwks_client: jwt.PyJWKClient | None = None


def _verify_id_token(id_token: str, nonce: str) -> dict:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(GOOGLE_JWKS, cache_keys=True)
    key = _jwks_client.get_signing_key_from_jwt(id_token).key
    claims = jwt.decode(id_token, key, algorithms=["RS256"], audience=get_settings().google_client_id,
                        options={"require": ["exp", "iat", "iss", "sub", "aud"]})
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise DomainError("Google sign-in failed (issuer)", 401)
    if claims.get("nonce") != nonce:
        raise DomainError("Google sign-in failed (nonce)", 401)
    return claims


def exchange_code(code: str) -> dict:
    s = get_settings()
    try:
        r = httpx.post(GOOGLE_TOKEN, timeout=20, data={
            "code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret,
            "redirect_uri": redirect_uri(), "grant_type": "authorization_code"})
    except httpx.HTTPError as exc:
        raise DomainError("Could not reach Google", 502) from exc
    if r.status_code != 200 or "id_token" not in r.json():
        raise DomainError("Google sign-in failed", 401)
    return r.json()


def read_state(state: str, binding: str | None) -> dict:
    data = _unsign(state)
    if not binding or not hmac.compare_digest(hashlib.sha256(binding.encode()).hexdigest(), data.get("b", "")):
        raise DomainError("Please finish in the same browser you started in.", 400)
    return data


def google_identity(code: str, data: dict, verify=None, exchange=None) -> tuple[dict, dict]:
    tokens = (exchange or exchange_code)(code)
    claims = (verify or _verify_id_token)(tokens["id_token"], data["n"])
    if not claims.get("email_verified"):
        raise DomainError("That Google email address is not verified.", 401)
    return tokens, claims


def google_callback(db: Session, *, code: str, state: str, binding: str | None, verify=None, exchange=None) -> User:
    """Sign in with Google. On a brand-new installation (no users yet) the first Google account becomes the owner."""
    data = read_state(state, binding)
    if data.get("k", "login") != "login":
        raise DomainError("Invalid sign-in state", 400)
    _, claims = google_identity(code, data, verify, exchange)
    email = claims["email"].lower()
    user = db.scalars(select(User).where(User.google_sub == claims["sub"])).first()
    if user is None:
        user = db.scalars(select(User).where(func.lower(User.email) == email)).first()
        if user is None:
            from app.services import bootstrap
            if bootstrap.needs_setup(db):
                return bootstrap.create_owner(db, email=email, full_name=claims.get("name") or email.split("@")[0],
                                              google_sub=claims["sub"], via="google")
            if data.get("m") == "signup":
                user = User(email=email, full_name=claims.get("name") or email.split("@")[0], google_sub=claims["sub"],
                            is_super_admin=False, password_hash=hash_password(secrets.token_urlsafe(32)))
                db.add(user)
                db.flush()
                audit(db, "auth.signed_up", user_id=user.id, via="google")
                return user
            audit(db, "auth.google_unknown_user", email_domain=email.split("@")[-1])
            raise DomainError(f"There is no account for {email} yet. Open the Sign up tab to create one.", 404)
        if user.google_sub and user.google_sub != claims["sub"]:
            raise DomainError("This account is linked to a different Google account.", 403)
        user.google_sub = claims["sub"]
        audit(db, "auth.google_linked", user_id=user.id)
    if not user.is_active:
        raise DomainError("Account disabled", 401)
    return user


def connect_mailbox(db: Session, *, code: str, state: str, binding: str | None, verify=None, exchange=None):
    """Store Gmail API access for a sender mailbox. The Google account must BE that mailbox."""
    import json

    from app.models import SenderAccount, SenderCredential
    from app.services.crypto import encrypt
    data = read_state(state, binding)
    if data.get("k") != "mailbox" or not data.get("s"):
        raise DomainError("Invalid request", 400)
    acc = db.get(SenderAccount, data["s"])
    if acc is None:
        raise DomainError("Mailbox not found", 404)
    tokens, claims = google_identity(code, data, verify, exchange)
    if claims["email"].lower() != acc.address.lower():
        raise DomainError(f"You signed in as {claims['email']}, but this mailbox is {acc.address}. "
                          "Choose that Google account instead.", 400)
    granted = set((tokens.get("scope") or "").split())
    if "https://www.googleapis.com/auth/gmail.send" not in granted:
        raise DomainError("Permission to send email wasn't granted. Try again and tick all the boxes.", 400)
    refresh = tokens.get("refresh_token")
    cred = db.scalars(select(SenderCredential).where(SenderCredential.sender_account_id == acc.id)).first()
    if not refresh and cred is None:
        raise DomainError("Google didn't return long-term access. Remove the app at myaccount.google.com/permissions "
                          "and connect again.", 400)
    if refresh:
        payload = encrypt(json.dumps({"refresh_token": refresh}))
        if cred is None:
            db.add(SenderCredential(sender_account_id=acc.id, kind="google_oauth", ciphertext=payload,
                                    scopes=" ".join(sorted(granted))))
        else:
            cred.ciphertext, cred.scopes = payload, " ".join(sorted(granted))
    acc.provider, acc.secret_ref = "gmail", f"db:{acc.id}"
    acc.connection_status, acc.health_status, acc.last_error = "connected", "healthy", None
    acc.verified_at, acc.verified_via = acc.verified_at or now_utc(), "google"
    if acc.status == "inactive":
        acc.status = "active"
    audit(db, "mailbox.google_connected", user_id=data.get("u"), brand_id=acc.brand_id, entity_type="sender",
          entity_id=acc.id)
    return acc
