import json
import re
from datetime import timedelta
from urllib.parse import unquote

import pytest

from app.core.config import get_settings
from app.core.secrets import resolve_secret
from app.models import AuthChallenge, SenderAccount, SenderCredential, User
from app.services import auth_flows
from app.services.common import DomainError
from app.services.mailer import DEV_OUTBOX
from app.services.routing import ineligibility_reason
from tests.conftest import auth


@pytest.fixture(autouse=True)
def _clear():
    DEV_OUTBOX.clear()
    yield


def code_for(addr: str) -> str:
    msg = [m for m in DEV_OUTBOX if m["to"] == addr][-1]
    return re.search(r"\b(\d{6})\b", msg["text"]).group(1)


def age_codes(db):
    for c in db.query(AuthChallenge).all():
        c.created_at -= timedelta(minutes=1)
    db.commit()


# ------------------------------------------------------------------ passwordless owner + login
def test_owner_signs_up_with_email_code(client, db):
    assert client.get("/api/auth/providers").json()["needs_owner"] is True
    start = client.post("/api/auth/email/start", json={"email": "Admin@99MarketingServices.com"}).json()
    assert start["challenge_id"] and start["email_hint"].startswith("ad")
    r = client.post("/api/auth/email/verify", json={"challenge_id": start["challenge_id"],
                                                    "code": code_for("admin@99marketingservices.com"),
                                                    "full_name": "Group Owner"})
    assert r.status_code == 200
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["is_super_admin"] and me["email"] == "admin@99marketingservices.com" and me["brands"] == []
    assert client.get("/api/auth/providers").json()["needs_owner"] is False


def test_second_person_cannot_become_owner(client, db):
    db.add(User(email="owner@x.io", full_name="O", password_hash="x", is_super_admin=True))
    db.commit()
    r = client.post("/api/auth/email/start", json={"email": "attacker@evil.io"}).json()
    assert DEV_OUTBOX == []  # nothing sent to unknown addresses
    bad = client.post("/api/auth/email/verify", json={"challenge_id": r["challenge_id"], "code": "123456"})
    assert bad.status_code == 401
    assert db.query(User).count() == 1


def test_passwordless_login_for_existing_user(client, db):
    db.add(User(email="team@x.io", full_name="T", password_hash="x"))
    db.commit()
    r = client.post("/api/auth/email/start", json={"email": "team@x.io"}).json()
    ok = client.post("/api/auth/email/verify", json={"challenge_id": r["challenge_id"], "code": code_for("team@x.io")})
    assert ok.status_code == 200 and ok.json()["access_token"]


def test_unknown_and_known_emails_look_the_same(client, db):
    db.add(User(email="team@x.io", full_name="T", password_hash="x"))
    db.commit()
    a = client.post("/api/auth/email/start", json={"email": "team@x.io"})
    b = client.post("/api/auth/email/start", json={"email": "nobody@x.io"})
    assert a.status_code == b.status_code == 200
    assert set(a.json()) == set(b.json()) and b.json()["mfa_required"] is True


# ------------------------------------------------------------------ brands + mailboxes
def test_brand_with_master_and_sub_mailboxes(client, db, world):
    h = auth(world["admin"])
    b = client.post("/api/brands", headers=h, json={"slug": "property-experts", "name": "Property Experts",
                                                     "master_email": "info@propertyexpertsgroup.com"}).json()
    assert b["master_email"] == "info@propertyexpertsgroup.com"
    sub = client.post(f"/api/brands/{b['id']}/mailboxes", headers=h,
                      json={"address": "LinkWithProperty1@gmail.com", "role": "sub"}).json()
    boxes = client.get(f"/api/brands/{b['id']}/mailboxes", headers=h).json()
    assert [(x["address"], x["role"]) for x in boxes] == [("info@propertyexpertsgroup.com", "master"),
                                                          ("linkwithproperty1@gmail.com", "sub")]
    assert all(not x["verified"] and x["connection_status"] == "pending" for x in boxes)
    # not connected => never used for sending
    acc = db.get(SenderAccount, sub["id"])
    assert ineligibility_reason(acc) is not None
    dup = client.post(f"/api/brands/{b['id']}/mailboxes", headers=h, json={"address": "linkwithproperty1@gmail.com"})
    assert dup.status_code == 409


def test_verify_mailbox_with_code(client, db, world):
    h = auth(world["admin"])
    b = client.post("/api/brands", headers=h, json={"slug": "pe", "name": "PE", "master_email": "info@pe.com"}).json()
    box = client.get(f"/api/brands/{b['id']}/mailboxes", headers=h).json()[0]
    st = client.post(f"/api/brands/{b['id']}/mailboxes/{box['id']}/verify/start", headers=h).json()
    assert st["sent_to"] == "info@pe.com"
    out = client.post(f"/api/brands/{b['id']}/mailboxes/{box['id']}/verify/confirm", headers=h,
                      json={"challenge_id": st["challenge_id"], "code": code_for("info@pe.com")}).json()
    assert out[0]["verified"] and out[0]["verified_via"] == "email_code"


def test_mailboxes_are_brand_isolated(client, db, world):
    b = world["b"]
    acc = SenderAccount(brand_id=b.id, channel="email", address="x@b.io", provider="gmail", status="inactive",
                        connection_status="pending", provider_config={})
    db.add(acc)
    db.commit()
    h = auth(world["agent_a"])
    assert client.get(f"/api/brands/{b.id}/mailboxes", headers=h).status_code == 403
    r = client.post(f"/api/brands/{world['a'].id}/mailboxes/{acc.id}/verify/start", headers=auth(world["admin"]))
    assert r.status_code == 404


@pytest.fixture
def google_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(s, "google_client_secret", "test-secret")
    monkeypatch.setattr(s, "public_app_url", "https://app.example.com")
    yield


def _connect_state(acc_id):
    url, binding = auth_flows.google_start("mailbox", user_id=1, sender_account_id=acc_id, login_hint="x")
    assert "gmail.send" in unquote(url) and "access_type=offline" in url
    return unquote(re.search(r"state=([^&]+)", url).group(1)), binding


def _ok_verify(email):
    return lambda _t, nonce: {"sub": "g-9", "email": email, "email_verified": True, "nonce": nonce}


TOKENS = {"id_token": "t", "refresh_token": "1//refresh-abc", "scope":
          "openid https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly"}


def test_connect_mailbox_with_google_stores_encrypted_token(db, world, google_on):
    acc = SenderAccount(brand_id=world["a"].id, channel="email", address="linkwithproperty1@gmail.com",
                        provider="gmail", status="inactive", connection_status="pending", provider_config={})
    db.add(acc)
    db.commit()
    state, binding = _connect_state(acc.id)
    auth_flows.connect_mailbox(db, code="c", state=state, binding=binding, verify=_ok_verify(acc.address),
                               exchange=lambda c: TOKENS)
    db.commit()
    db.refresh(acc)
    assert acc.connection_status == "connected" and acc.status == "active" and acc.verified_via == "google"
    assert acc.secret_ref == f"db:{acc.id}" and ineligibility_reason(acc) is None
    cred = db.query(SenderCredential).one()
    assert "refresh-abc" not in cred.ciphertext  # stored encrypted
    creds = json.loads(resolve_secret(acc.secret_ref))
    assert creds == {"client_id": "cid.apps.googleusercontent.com", "client_secret": "test-secret",
                     "refresh_token": "1//refresh-abc"}


def test_connect_mailbox_refuses_a_different_google_account(db, world, google_on):
    acc = SenderAccount(brand_id=world["a"].id, channel="email", address="info@pe.com", provider="gmail",
                        status="inactive", connection_status="pending", provider_config={})
    db.add(acc)
    db.commit()
    state, binding = _connect_state(acc.id)
    with pytest.raises(DomainError) as e:
        auth_flows.connect_mailbox(db, code="c", state=state, binding=binding, verify=_ok_verify("someone@gmail.com"),
                                   exchange=lambda c: TOKENS)
    assert "Choose that Google account" in str(e.value)


def test_connect_mailbox_requires_send_permission(db, world, google_on):
    acc = SenderAccount(brand_id=world["a"].id, channel="email", address="info@pe.com", provider="gmail",
                        status="inactive", connection_status="pending", provider_config={})
    db.add(acc)
    db.commit()
    state, binding = _connect_state(acc.id)
    with pytest.raises(DomainError):
        auth_flows.connect_mailbox(db, code="c", state=state, binding=binding, verify=_ok_verify("info@pe.com"),
                                   exchange=lambda c: {**TOKENS, "scope": "openid email"})


def test_login_state_cannot_be_used_to_connect_mailbox_and_vice_versa(db, world, google_on):
    url, binding = auth_flows.google_start("login")
    state = unquote(re.search(r"state=([^&]+)", url).group(1))
    with pytest.raises(DomainError):
        auth_flows.connect_mailbox(db, code="c", state=state, binding=binding, verify=_ok_verify("a@b.io"),
                                   exchange=lambda c: TOKENS)
    mstate, mbind = _connect_state(world["senders_a"][0].id)
    with pytest.raises(DomainError):
        auth_flows.google_callback(db, code="c", state=mstate, binding=mbind, verify=_ok_verify("a@b.io"),
                                   exchange=lambda c: TOKENS)


def test_mailbox_google_start_endpoint_sets_cookie(client, db, world, google_on):
    h = auth(world["admin"])
    b = client.post("/api/brands", headers=h, json={"slug": "pe2", "name": "PE2", "master_email": "info@pe2.com"}).json()
    box = client.get(f"/api/brands/{b['id']}/mailboxes", headers=h).json()[0]
    r = client.post(f"/api/brands/{b['id']}/mailboxes/{box['id']}/google/start", headers=h)
    assert r.status_code == 200 and r.json()["url"].startswith("https://accounts.google.com/")
    assert "login_hint=info%40pe2.com" in r.json()["url"]
    assert "g_bind=" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
