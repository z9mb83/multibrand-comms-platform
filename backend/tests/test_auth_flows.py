import re

import pytest

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import User
from app.services import auth_flows
from app.services.common import DomainError
from app.services.mailer import DEV_OUTBOX
from tests.conftest import auth


@pytest.fixture(autouse=True)
def _clear_outbox():
    DEV_OUTBOX.clear()
    yield


def last_code() -> str:
    return re.search(r"\b(\d{6})\b", DEV_OUTBOX[-1]["text"]).group(1)


def make_user(db, email="otp@brand.io", mfa=False, **kw):
    u = User(email=email, full_name="Otp User", password_hash=hash_password("password123"), mfa_email_enabled=mfa, **kw)
    db.add(u)
    db.commit()
    return u


def test_login_without_mfa_returns_token(client, db):
    make_user(db)
    r = client.post("/api/auth/login", json={"email": "otp@brand.io", "password": "password123"}).json()
    assert r["access_token"] and r["mfa_required"] is False
    assert DEV_OUTBOX == []


def test_login_with_email_code(client, db):
    make_user(db, mfa=True)
    r = client.post("/api/auth/login", json={"email": "otp@brand.io", "password": "password123"}).json()
    assert r["mfa_required"] and r["access_token"] is None and r["email_hint"].startswith("ot")
    assert DEV_OUTBOX[-1]["to"] == "otp@brand.io"
    code = last_code()
    bad = "000000" if code != "000000" else "111111"
    wrong = client.post("/api/auth/otp/verify", json={"challenge_id": r["challenge_id"], "code": bad})
    assert wrong.status_code == 401 and "4 attempts left" in wrong.json()["detail"]
    ok = client.post("/api/auth/otp/verify", json={"challenge_id": r["challenge_id"], "code": code})
    assert ok.status_code == 200 and ok.json()["access_token"]
    # single use
    again = client.post("/api/auth/otp/verify", json={"challenge_id": r["challenge_id"], "code": code})
    assert again.status_code == 401


def test_code_locks_after_max_attempts(client, db):
    make_user(db, mfa=True)
    r = client.post("/api/auth/login", json={"email": "otp@brand.io", "password": "password123"}).json()
    code = last_code()
    bad = "000000" if code != "000000" else "111111"
    for _ in range(5):
        client.post("/api/auth/otp/verify", json={"challenge_id": r["challenge_id"], "code": bad})
    locked = client.post("/api/auth/otp/verify", json={"challenge_id": r["challenge_id"], "code": code})
    assert locked.status_code == 429


def test_code_rate_limits(db):
    u = make_user(db, mfa=True)
    auth_flows.issue_code(db, u, "login")
    with pytest.raises(DomainError) as e:
        auth_flows.issue_code(db, u, "login")  # within 30 seconds
    assert e.value.status_code == 429


def test_code_is_not_stored_in_plaintext(db):
    u = make_user(db, mfa=True)
    ch = auth_flows.issue_code(db, u, "login")
    assert last_code() not in ch.code_hash and len(ch.code_hash) == 64


def test_enable_and_disable_email_codes_require_a_code(client, db):
    u = make_user(db)
    h = auth(u)
    start = client.post("/api/auth/mfa/email/start", headers=h, json={"action": "enable"}).json()
    assert client.get("/api/auth/me", headers=h).json()["mfa_email_enabled"] is False  # not until confirmed
    me = client.post("/api/auth/mfa/email/confirm", headers=h, json={"challenge_id": start["challenge_id"],
                                                                     "code": last_code()}).json()
    assert me["mfa_email_enabled"] is True
    db.expire_all()
    ch = client.post("/api/auth/mfa/email/start", headers=h, json={"action": "disable"})
    assert ch.status_code == 429  # 30s resend gap also protects the settings flow
    from datetime import timedelta

    from app.models import AuthChallenge
    for c in db.query(AuthChallenge).all():
        c.created_at -= timedelta(minutes=1)
    db.commit()
    ch = client.post("/api/auth/mfa/email/start", headers=h, json={"action": "disable"}).json()
    me = client.post("/api/auth/mfa/email/confirm", headers=h, json={"challenge_id": ch["challenge_id"],
                                                                     "code": last_code()}).json()
    assert me["mfa_email_enabled"] is False


def test_cannot_use_someone_elses_challenge(client, db):
    a = make_user(db, email="a@brand.io")
    b = make_user(db, email="b@brand.io")
    start = client.post("/api/auth/mfa/email/start", headers=auth(a), json={"action": "enable"}).json()
    r = client.post("/api/auth/mfa/email/confirm", headers=auth(b), json={"challenge_id": start["challenge_id"],
                                                                          "code": last_code()})
    assert r.status_code == 400


def test_super_admin_can_reset_mfa(client, db, world):
    u = make_user(db, mfa=True)
    r = client.post(f"/api/users/{u.id}/mfa/reset", headers=auth(world["admin"]))
    assert r.status_code == 200
    assert client.post(f"/api/users/{u.id}/mfa/reset", headers=auth(world["agent_a"])).status_code == 403


# ---------------------------------------------------------------- Google
@pytest.fixture
def google_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(s, "google_client_secret", "test-secret")
    monkeypatch.setattr(s, "public_app_url", "https://app.example.com")
    yield


def _start():
    url, binding = auth_flows.google_start()
    state = re.search(r"state=([^&]+)", url).group(1)
    from urllib.parse import unquote
    return unquote(state), binding, url


def _claims(sub="g-123", email="otp@brand.io", verified=True):
    return lambda _tok, nonce: {"sub": sub, "email": email, "email_verified": verified, "nonce": nonce}


def test_providers_endpoint(client, google_on):
    assert client.get("/api/auth/providers").json()["google"] is True


def test_google_start_url(google_on):
    _, _, url = _start()
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "redirect_uri=https%3A%2F%2Fapp.example.com%2Fapi%2Fauth%2Fgoogle%2Fcallback" in url
    assert "scope=openid+email+profile" in url and "nonce=" in url


def test_google_links_existing_user_by_verified_email(db, google_on):
    u = make_user(db)
    state, binding, _ = _start()
    got = auth_flows.google_callback(db, code="c", state=state, binding=binding, verify=_claims(),
                                     exchange=lambda c: {"id_token": "tok"})
    assert got.id == u.id and got.google_sub == "g-123"


def test_google_sign_in_does_not_create_accounts(db, google_on):
    make_user(db, email="owner@brand.io")
    state, binding, _ = _start()
    with pytest.raises(DomainError) as e:
        auth_flows.google_callback(db, code="c", state=state, binding=binding, verify=_claims(email="stranger@x.io"),
                                   exchange=lambda c: {"id_token": "tok"})
    assert e.value.status_code == 404 and "Sign up" in str(e.value)
    assert db.query(User).count() == 1


def test_google_sign_up_creates_an_account_without_access(db, google_on):
    from urllib.parse import unquote
    make_user(db, email="owner@brand.io")
    url, binding = auth_flows.google_start(mode="signup")
    state = unquote(re.search(r"state=([^&]+)", url).group(1))
    u = auth_flows.google_callback(db, code="c", state=state, binding=binding, verify=_claims(email="New@x.io"),
                                   exchange=lambda c: {"id_token": "tok"})
    assert u.email == "new@x.io" and not u.is_super_admin and u.memberships == []


def test_first_google_account_becomes_owner_on_new_install(db, google_on):
    state, binding, _ = _start()
    u = auth_flows.google_callback(db, code="c", state=state, binding=binding, verify=_claims(email="Admin@99m.com"),
                                   exchange=lambda c: {"id_token": "tok"})
    db.commit()
    assert u.is_super_admin and u.email == "admin@99m.com" and u.google_sub == "g-123"


def test_google_rejects_unverified_email(db, google_on):
    make_user(db)
    state, binding, _ = _start()
    with pytest.raises(DomainError):
        auth_flows.google_callback(db, code="c", state=state, binding=binding, verify=_claims(verified=False),
                                   exchange=lambda c: {"id_token": "tok"})


def test_google_state_must_match_browser_and_signature(db, google_on):
    make_user(db)
    state, binding, _ = _start()
    with pytest.raises(DomainError):  # callback finished in a different browser (no/other cookie)
        auth_flows.google_callback(db, code="c", state=state, binding="other", verify=_claims(), exchange=lambda c: {"id_token": "t"})
    with pytest.raises(DomainError):  # forged state
        auth_flows.google_callback(db, code="c", state=state[:-2] + "xx", binding=binding, verify=_claims(),
                                   exchange=lambda c: {"id_token": "t"})


def test_google_account_mismatch_is_refused(db, google_on):
    make_user(db, google_sub="g-original")
    state, binding, _ = _start()
    with pytest.raises(DomainError) as e:
        auth_flows.google_callback(db, code="c", state=state, binding=binding, verify=_claims(sub="g-other"),
                                   exchange=lambda c: {"id_token": "t"})
    assert "different Google account" in str(e.value)


def test_google_callback_route_with_mfa_redirects_to_code_step(client, db, google_on, monkeypatch):
    make_user(db, mfa=True)
    monkeypatch.setattr(auth_flows, "exchange_code", lambda c: "tok")
    monkeypatch.setattr(auth_flows, "_verify_id_token", _claims())
    # route passes defaults captured at import time; call through the service with patched functions
    state, binding, _ = _start()
    orig = auth_flows.google_callback
    monkeypatch.setattr(auth_flows, "google_callback",
                        lambda db, **kw: orig(db, **kw, verify=_claims(), exchange=lambda c: {"id_token": "tok"}))
    client.cookies.set("g_bind", binding, path="/api/auth/google")
    r = client.get("/api/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/login?mfa=")
    assert DEV_OUTBOX and DEV_OUTBOX[-1]["to"] == "otp@brand.io"


def test_google_callback_route_without_mfa_returns_token_in_fragment(client, db, google_on, monkeypatch):
    make_user(db)
    state, binding, _ = _start()
    orig = auth_flows.google_callback
    monkeypatch.setattr(auth_flows, "google_callback",
                        lambda db, **kw: orig(db, **kw, verify=_claims(), exchange=lambda c: {"id_token": "tok"}))
    client.cookies.set("g_bind", binding, path="/api/auth/google")
    r = client.get("/api/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.headers["location"].startswith("/login#token=")


def test_google_callback_errors_redirect_with_message(client, google_on):
    r = client.get("/api/auth/google/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r.status_code == 302 and "/login?error=" in r.headers["location"]


def test_id_token_signature_audience_issuer_nonce(google_on, monkeypatch):
    import time as _t

    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJwks:
        def get_signing_key_from_jwt(self, _tok):
            return type("K", (), {"key": key.public_key()})()

    monkeypatch.setattr(auth_flows, "_jwks_client", FakeJwks())
    base = {"sub": "g-1", "email": "a@b.io", "email_verified": True, "nonce": "n1",
            "aud": "cid.apps.googleusercontent.com", "iss": "https://accounts.google.com",
            "iat": int(_t.time()), "exp": int(_t.time()) + 300}
    good = pyjwt.encode(base, key, algorithm="RS256")
    assert auth_flows._verify_id_token(good, "n1")["sub"] == "g-1"
    for bad_claims, nonce, signer in [({"aud": "someone-else"}, "n1", key), ({}, "wrong-nonce", key),
                                      ({"iss": "https://evil.example"}, "n1", key), ({}, "n1", other),
                                      ({"exp": int(_t.time()) - 10}, "n1", key)]:
        tok = pyjwt.encode({**base, **bad_claims}, signer, algorithm="RS256")
        with pytest.raises((pyjwt.PyJWTError, DomainError)):
            auth_flows._verify_id_token(tok, nonce)
