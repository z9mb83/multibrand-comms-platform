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


def issue_code(db: Session, user: User, purpose: str, method: str = "password") -> AuthChallenge:
    s = get_settings()
    now = now_utc()
    recent = db.scalars(select(AuthChallenge).where(AuthChallenge.user_id == user.id,
                                                    AuthChallenge.created_at > now - timedelta(minutes=15))
                        .order_by(AuthChallenge.created_at.desc())).all()
    if len(recent) >= 5:
        raise DomainError("Too many codes requested. Try again in 15 minutes.", 429)
    if recent and (now - recent[0].created_at).total_seconds() < 30:
        raise DomainError("Please wait 30 seconds before requesting another code.", 429)
    code = f"{secrets.randbelow(1_000_000):06d}"
    ch = AuthChallenge(id=str(uuid.uuid4()), user_id=user.id, purpose=purpose, method=method, attempts=0,
                       expires_at=now + timedelta(minutes=s.otp_ttl_minutes), created_at=now)
    ch.code_hash = _hash_code(ch.id, code)
    db.add(ch)
    db.flush()
    what = "sign in to" if purpose == "login" else "turn on email codes for"
    text = (f"Your Comms Hub code is {code}\n\nUse it to {what} your account. It expires in {s.otp_ttl_minutes} minutes.\n"
            "If you didn't try to sign in, change your password and tell your administrator.")
    html = (f"<p>Your Comms Hub code is</p><p style='font-size:28px;font-weight:700;letter-spacing:6px'>{code}</p>"
            f"<p>Use it to {what} your account. It expires in {s.otp_ttl_minutes} minutes.</p>"
            "<p style='color:#666'>If you didn't try to sign in, change your password and tell your administrator.</p>")
    try:
        send_email(user.email, f"{code} is your Comms Hub code", text, html)
    except MailerError as exc:
        raise DomainError(f"Could not send the code: {exc}", 503) from exc
    audit(db, "auth.otp_sent", user_id=user.id, purpose=purpose)
    return ch


def verify_code(db: Session, challenge_id: str, code: str, purpose: str) -> User:
    s = get_settings()
    ch = db.scalars(select(AuthChallenge).where(AuthChallenge.id == challenge_id).with_for_update()).first()
    if ch is None or ch.purpose != purpose or ch.consumed_at is not None or ch.expires_at < now_utc():
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
    user = db.get(User, ch.user_id)
    if user is None or not user.is_active:
        raise DomainError("Account disabled", 401)
    return user


def resend_code(db: Session, challenge_id: str) -> AuthChallenge:
    ch = db.get(AuthChallenge, challenge_id)
    if ch is None or ch.consumed_at is not None:
        raise DomainError("Start signing in again.", 401)
    user = db.get(User, ch.user_id)
    ch.consumed_at = now_utc()  # the old code stops working
    return issue_code(db, user, ch.purpose, ch.method)


def mfa_available() -> bool:
    return can_deliver()


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


def google_start() -> tuple[str, str]:
    """Returns (authorization URL, browser-binding value to store in an HttpOnly cookie)."""
    if not google_enabled():
        raise DomainError("Sign in with Google is not configured", 404)
    binding = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(16)
    state = _sign({"b": hashlib.sha256(binding.encode()).hexdigest(), "n": nonce, "exp": int(time.time()) + STATE_TTL})
    params = {"client_id": get_settings().google_client_id, "redirect_uri": redirect_uri(), "response_type": "code",
              "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account"}
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


def exchange_code(code: str) -> str:
    s = get_settings()
    try:
        r = httpx.post(GOOGLE_TOKEN, timeout=20, data={
            "code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret,
            "redirect_uri": redirect_uri(), "grant_type": "authorization_code"})
    except httpx.HTTPError as exc:
        raise DomainError("Could not reach Google", 502) from exc
    if r.status_code != 200 or "id_token" not in r.json():
        raise DomainError("Google sign-in failed", 401)
    return r.json()["id_token"]


def google_callback(db: Session, *, code: str, state: str, binding: str | None,
                    verify=_verify_id_token, exchange=exchange_code) -> User:
    data = _unsign(state)
    if not binding or not hmac.compare_digest(hashlib.sha256(binding.encode()).hexdigest(), data.get("b", "")):
        raise DomainError("Sign-in must be finished in the same browser it was started in.", 400)
    claims = verify(exchange(code), data["n"])
    if not claims.get("email_verified"):
        raise DomainError("Your Google email address is not verified.", 401)
    email = claims["email"].lower()
    user = db.scalars(select(User).where(User.google_sub == claims["sub"])).first()
    if user is None:
        user = db.scalars(select(User).where(func.lower(User.email) == email)).first()
        if user is None:
            audit(db, "auth.google_unknown_user", email_domain=email.split("@")[-1])
            raise DomainError("No Comms Hub account uses this Google address. Ask your administrator to add you.", 403)
        if user.google_sub and user.google_sub != claims["sub"]:
            raise DomainError("This account is linked to a different Google account.", 403)
        user.google_sub = claims["sub"]
        audit(db, "auth.google_linked", user_id=user.id)
    if not user.is_active:
        raise DomainError("Account disabled", 401)
    return user
