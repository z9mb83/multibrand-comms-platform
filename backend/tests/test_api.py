"""End-to-end API tests: auth, brand isolation, import wizard flow, approval-gated sending."""
import io

from openpyxl import Workbook

from app.providers.mock import OUTBOX
from tests.conftest import add_contact, auth


def test_login_and_me(client, world):
    r = client.post("/api/auth/login", json={"email": "agent@marketing.io", "password": "password123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert [b["slug"] for b in me["brands"]] == ["marketing"]
    assert client.post("/api/auth/login", json={"email": "agent@marketing.io", "password": "nope"}).status_code == 401


def test_brand_isolation(client, db, world):
    a, b, agent = world["a"], world["b"], world["agent_a"]
    add_contact(db, b, email="secret@b.test")
    db.commit()
    h = auth(agent)
    assert client.get(f"/api/brands/{b.id}/contacts", headers=h).status_code == 403
    assert client.get(f"/api/brands/{b.id}/senders", headers=h).status_code == 403
    assert client.get(f"/api/brands/{a.id}/senders", headers=h).status_code == 200
    assert [x["slug"] for x in client.get("/api/brands", headers=h).json()] == ["marketing"]
    # cannot use another brand's contact via own brand path
    cid = db.execute(__import__("sqlalchemy").text("select contact_id from brand_contacts where email='secret@b.test'")).scalar()
    r = client.post(f"/api/brands/{a.id}/compose", headers=h, json={"contact_id": cid, "channel": "email", "body": "x"})
    assert r.status_code == 404
    # unified inbox only shows accessible brands
    assert client.get(f"/api/inbox?brand_id={b.id}", headers=h).status_code == 403
    # master settings are super admin only
    assert client.get("/api/master/settings", headers=h).status_code == 403
    assert client.get("/api/master/settings", headers=auth(world["admin"])).status_code == 200


def test_super_admin_sees_all_brands_and_analytics(client, world):
    h = auth(world["admin"])
    assert len(client.get("/api/brands", headers=h).json()) == 2
    ov = client.get("/api/analytics/overview", headers=h).json()
    assert {x["brand"] for x in ov["brands"]} == {"Marketing Co", "Staffing Co"}
    assert ov["totals"]["active_senders"] == 11


def test_routing_overview_shows_next_sender(client, world):
    a = world["a"]
    r = client.get(f"/api/brands/{a.id}/senders/routing/overview?channel=email", headers=auth(world["admin"])).json()
    assert r["active_senders"] == 5 and r["next_sender"]["address"] == world["senders_a"][0].address


def test_import_wizard_flow_requires_campaign_approval(client, world):
    h = auth(world["agent_a"])
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append(["Contact Name", "Email", "Message", "Subject", "Campaign", "Email Consent"])
    ws.append(["Asha", "asha@x.test", "Hello Asha", "Hi", "Sept", "yes"])
    ws.append(["Ravi", "ravi@x.test", "Hello Ravi", "Hi", "Sept", "yes"])
    buf = io.BytesIO()
    wb.save(buf)
    up = client.post("/api/imports/upload", headers=h, data={"brand_id": str(world["a"].id)},
                     files={"file": ("leads.xlsx", buf.getvalue(), "application/octet-stream")}).json()
    sheet = up["sheets"][0]
    assert sheet["name"] == "Leads" and sheet["detected_mapping"]["email"] == "Email"
    pv = client.post(f"/api/imports/{up['id']}/preview", headers=h,
                     json={"sheets": ["Leads"], "mapping": sheet["detected_mapping"],
                           "options": {"brand_mode": "single", "brand_id": world["a"].id}}).json()
    assert pv["counts"]["valid"] == 2
    cm = client.post(f"/api/imports/{up['id']}/commit", headers=h, json={}).json()
    assert cm["counts"]["queued_for_approval"] == 2
    assert OUTBOX.sent == []
    camp_id = cm["counts"]["campaign_ids"][0]
    base = f"/api/brands/{world['a'].id}/campaigns/{camp_id}/actions"
    assert client.post(base, headers=h, json={"action": "launch"}).status_code == 409  # not approved yet
    assert client.post(base, headers=h, json={"action": "approve"}).status_code == 200
    r = client.post(base, headers=h, json={"action": "launch"}).json()
    assert r["stats"] == {"sent": 2}
    assert sorted(x.to_address for x in OUTBOX.sent) == ["asha@x.test", "ravi@x.test"]
    assert len({x.from_address for x in OUTBOX.sent}) == 2  # round-robin across senders
    err = client.get(f"/api/imports/{up['id']}/errors.xlsx", headers=h)
    assert err.status_code == 200 and err.headers["content-type"].startswith("application/vnd.openxml")


def test_compose_fixed_sender_and_release_held(client, db, world):
    a, admin = world["a"], world["admin"]
    s = world["senders_a"]
    bc = add_contact(db, a, email="c@x.test")
    s[1].status = "paused"
    db.commit()
    h = auth(admin)
    r = client.post(f"/api/brands/{a.id}/compose", headers=h,
                    json={"contact_id": bc.contact_id, "channel": "email", "subject": "s", "body": "b",
                          "sender_id": s[1].id}).json()
    assert r["status"] == "held_for_review" and r["routing_mode"] == "FIXED"
    r2 = client.post(f"/api/brands/{a.id}/messages/{r['id']}/release", headers=h, json={"sender_id": s[2].id}).json()
    assert r2["status"] == "sent" and r2["sender_account_id"] == s[2].id


def test_webhook_email_inbound_and_inbox(client, db, world):
    a = world["a"]
    add_contact(db, a, email="lead@x.test")
    db.commit()
    tok = {"X-Internal-Token": "local-internal-token"}
    r = client.post("/webhooks/email/inbound", headers=tok,
                    json={"mailbox": world["senders_a"][1].address, "from_address": "lead@x.test",
                          "message_id": "gm-1", "subject": "Re: offer", "body": "What is the price?"})
    assert r.status_code == 200 and r.json()["brand_id"] == a.id
    inbox = client.get("/api/inbox", headers=auth(world["admin"])).json()
    assert inbox[0]["classification"] == "Needs information" and inbox[0]["brand_name"] == "Marketing Co"
    assert client.post("/webhooks/email/inbound", json={"mailbox": "x", "from_address": "y", "message_id": "z"}).status_code == 401


def test_whatsapp_webhook(client, db, world):
    wa = world["wa_a"]
    wa.provider_config = {"phone_number_id": "PNID1"}
    add_contact(db, world["a"], phone="+919812300000", consent=("whatsapp",))
    db.commit()
    assert client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "local-verify-token",
                                                    "hub.challenge": "42"}).text == "42"
    payload = {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "PNID1"},
                                                  "messages": [{"from": "919812300000", "id": "wamid.1", "timestamp": "1790000000",
                                                                "type": "text", "text": {"body": "Interested!"}}]}}]}]}
    r = client.post("/webhooks/whatsapp", json=payload).json()
    assert r["received"] == 1
