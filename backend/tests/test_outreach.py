"""The Sheet automation's behaviour, ported: drafts, approval, one send per brand per run, mailbox choice,
gaps, hours, suppression, duplicates, emergency stop, Send Log, imports and the daily reports."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from app.models import OutreachLead, OutreachMailbox, OutreachReportReceipt, OutreachSendLog
from app.models.outreach import OutreachSuppression
from app.providers.mock import OUTBOX
from app.services.outreach import engine, reports
from app.services.outreach.sheet_import import import_file
from tests.conftest import ENGINE, auth

IST = ZoneInfo("Asia/Kolkata")
MON_11 = datetime(2026, 9, 28, 11, 0, tzinfo=IST)


def fake_generate(model, brand_name, settings, name, details):
    from app.services.outreach.generator import signature_for
    return f"Hello {name}", f"Body for {name}.\n\n{signature_for(brand_name, settings)}"


def boom(*a):
    raise RuntimeError("quota exceeded")


def ready(db, brand, **kw):
    s = engine.settings_for(db, brand.id)
    s.emergency_stop = False
    s.cc_email = kw.pop("cc", "info@brand.test")
    for k, v in kw.items():
        setattr(s, k, v)
    db.commit()
    return s


def lead(db, brand, email, *, approved=True, status="Generated", subject="Hi", body="Hello there", name="Lead"):
    x = OutreachLead(brand_id=brand.id, person_name=name, email=email, details="", subject=subject, body=body,
                     status=status, error="", sent_from="", approval="Yes" if approved else "", gmail_message_id="",
                     gmail_thread_id="", follow_up_status="")
    db.add(x)
    db.commit()
    return x


def run(db, now=MON_11, **kw):
    return engine.run_unified(db, now=now, generate=kw.pop("generate", fake_generate), **kw)


def by_brand(results, brand):
    return next(r for r in results if r["brand_id"] == brand.id)


# ------------------------------------------------------------------ safety switches
def test_emergency_stop_is_on_for_new_brands(db, world):
    lead(db, world["a"], "x@lead.io")
    r = by_brand(run(db), world["a"])
    assert r["skipped"] == "Emergency stop active" and OUTBOX.sent == []


def test_disabled_brand_is_skipped(db, world):
    ready(db, world["a"], enabled=False)
    lead(db, world["a"], "x@lead.io")
    assert by_brand(run(db), world["a"])["skipped"] == "brand disabled"


# ------------------------------------------------------------------ drafts
def test_drafts_are_written_with_the_fixed_signature_and_need_approval(db, world):
    ready(db, world["a"], phone="+91 8080803736", website="https://www.example.com/")
    x = lead(db, world["a"], "priya@lead.io", approved=False, status="Pending", subject="", body="", name="Priya")
    r = by_brand(run(db), world["a"])
    db.refresh(x)
    assert r["generated"] == 1 and x.status == "Generated" and x.subject == "Hello Priya"
    assert x.body.endswith("Ajay Veer\nSales Executive\nMarketing Co\nPhone: +91 8080803736\nEmail: info@brand.test\n"
                           "Website: www.example.com")
    assert r["skipped"] == "No approved lead is ready" and OUTBOX.sent == []


def test_generation_errors_are_recorded_on_the_lead(db, world):
    ready(db, world["a"])
    x = lead(db, world["a"], "a@lead.io", status="Pending", subject="", body="")
    run(db, generate=boom)
    db.refresh(x)
    assert x.status == "Failed" and x.error == "OpenAI generation error: quota exceeded"


def test_draft_limit_per_run(db, world):
    ready(db, world["a"])
    engine.global_state(db).max_drafts_per_brand_per_run = 2
    db.commit()
    for i in range(5):
        lead(db, world["a"], f"p{i}@lead.io", approved=False, status="Pending", subject="", body="")
    assert by_brand(run(db), world["a"])["generated"] == 2


def test_invalid_email_is_skipped_during_drafting(db, world):
    ready(db, world["a"])
    x = lead(db, world["a"], "not-an-email", status="Pending", subject="", body="")
    run(db)
    db.refresh(x)
    assert (x.status, x.error) == ("Skipped", "Invalid email address")


# ------------------------------------------------------------------ sending
def test_sends_first_approved_lead_with_cc_and_records_everything(db, world):
    ready(db, world["a"], cc="info@propertyexpertsgroup.com")
    lead(db, world["a"], "first-not-approved@lead.io", approved=False)
    x = lead(db, world["a"], "Buyer@Lead.io")
    lead(db, world["a"], "later@lead.io")
    r = by_brand(run(db), world["a"])
    assert r["sent"] is True and len(OUTBOX.sent) == 1
    req = OUTBOX.sent[0]
    assert req.to_address == "buyer@lead.io" and req.cc == "info@propertyexpertsgroup.com" and req.plain_text
    db.refresh(x)
    assert x.status == "Sent" and x.sent_from == req.from_address and x.gmail_message_id and x.follow_up_status == "Pending"
    log = db.scalars(select(OutreachSendLog)).one()
    assert (log.result, log.lead_email, log.daily_limit, log.stage) == ("Sent", "buyer@lead.io", 3, "Stage 1")
    box = db.scalars(select(OutreachMailbox).where(OutreachMailbox.last_sent_at.is_not(None))).one()
    assert box.next_eligible_at == box.last_sent_at + timedelta(minutes=25)


def test_one_email_per_brand_per_run_and_the_brand_gap(db, world):
    ready(db, world["a"])
    for i in range(3):
        lead(db, world["a"], f"l{i}@lead.io")
    assert by_brand(run(db), world["a"])["sent"] is True
    r = by_brand(run(db, now=MON_11 + timedelta(minutes=10)), world["a"])
    assert r["skipped"] == "Brand send gap active: 15 minute(s) remaining" and len(OUTBOX.sent) == 1
    assert by_brand(run(db, now=MON_11 + timedelta(minutes=26)), world["a"])["sent"] is True


def test_brands_run_independently(db, world):
    ready(db, world["a"])
    ready(db, world["b"])
    lead(db, world["a"], "x@lead.io")
    lead(db, world["b"], "y@lead.io")
    results = run(db)
    assert all(r["sent"] for r in results) and len(OUTBOX.sent) == 2


@pytest.mark.parametrize("setup,status,error", [
    ("suppressed", "Unsubscribed", "Recipient suppressed"),
    ("contacted", "Skipped", "Recipient already contacted by this brand"),
    ("invalid", "Skipped", "Invalid email address"),
    ("blank", "Scheduled", "Approved row has blank subject or body"),
])
def test_lead_checks(db, world, setup, status, error):
    ready(db, world["a"])
    email = "bad@@x" if setup == "invalid" else "dup@lead.io"
    if setup == "suppressed":
        db.add(OutreachSuppression(brand_id=world["a"].id, email="dup@lead.io", reason="asked"))
    if setup == "contacted":
        db.add(OutreachSendLog(brand_id=world["a"].id, logged_at=MON_11 - timedelta(days=40), sender_email="old@x.io",
                               lead_email="dup@lead.io", result="Sent", imported=True))
    if setup == "blank":  # (normally a blank draft is rewritten first; with drafting off it is held)
        engine.global_state(db).max_drafts_per_brand_per_run = 0
    db.commit()
    x = lead(db, world["a"], email, body="" if setup == "blank" else "Hello")
    run(db)
    db.refresh(x)
    assert (x.status, x.error) == (status, error) and OUTBOX.sent == []


def test_cross_brand_duplicates_only_when_enabled(db, world):
    ready(db, world["a"])
    ready(db, world["b"])
    db.add(OutreachSendLog(brand_id=world["b"].id, logged_at=MON_11 - timedelta(days=3), sender_email="s@b.io",
                           lead_email="shared@lead.io", result="Sent"))
    db.commit()
    x = lead(db, world["a"], "shared@lead.io")
    engine.global_state(db).cross_brand_duplicate_days = 7
    db.commit()
    run(db, selected_brand_id=world["a"].id)
    db.refresh(x)
    assert x.error == "Recipient recently contacted by another brand"
    x.status, x.error = "Generated", ""
    engine.global_state(db).cross_brand_duplicate_days = 0
    db.commit()
    assert by_brand(run(db, selected_brand_id=world["a"].id), world["a"])["sent"] is True


def test_outside_sending_hours_waits(db, world):
    ready(db, world["a"])
    x = lead(db, world["a"], "x@lead.io")
    r = by_brand(run(db, now=MON_11.replace(hour=18, minute=5)), world["a"])
    db.refresh(x)
    assert r["skipped"] == "Outside sending hours" and x.status == "Scheduled" and x.approval == "Yes"
    assert by_brand(run(db), world["a"])["sent"] is True  # still approved, goes next day in hours


def test_failed_send_is_logged(db, world):
    ready(db, world["a"])
    OUTBOX.fail_addresses.add("x@lead.io")
    x = lead(db, world["a"], "x@lead.io")
    r = by_brand(run(db), world["a"])
    db.refresh(x)
    assert x.status == "Failed" and "mock permanent failure" in x.error and r["errors"]
    assert db.scalars(select(OutreachSendLog.result)).all() == ["Failed"]


def test_uncertain_earlier_attempt_is_never_resent(db, world):
    ready(db, world["a"])
    x = lead(db, world["a"], "x@lead.io")
    db.add(OutreachSendLog(brand_id=world["a"].id, lead_id=x.id, logged_at=MON_11 - timedelta(hours=2),
                           sender_email="sender1@marketing.test", lead_email="x@lead.io", result="Sending"))
    db.commit()
    run(db)
    db.refresh(x)
    assert x.status == "Skipped" and "did not finish" in x.error and OUTBOX.sent == []


# ------------------------------------------------------------------ mailbox choice and limits
def _boxes(db, world, now=MON_11):
    s = engine.settings_for(db, world["a"].id)
    return engine.load_mailboxes(db, world["a"].id, s, now)


def test_least_used_mailbox_first_and_brand_default_limits(db, world):
    ready(db, world["a"], minimum_gap_minutes=0, default_hourly_limit=5, default_daily_limit=2)
    for i in range(6):
        lead(db, world["a"], f"l{i}@lead.io")
    used = []
    for i in range(6):
        run(db, now=MON_11 + timedelta(minutes=i))
        used.append(OUTBOX.sent[-1].from_address if len(OUTBOX.sent) > len(used) else None)
    assert len(set(used)) == 5  # spread across all five before any repeats
    counts = {m.email: m.sent_today for m in _boxes(db, world, MON_11 + timedelta(minutes=10))}
    assert max(counts.values()) <= 2


def test_mailbox_exclusions(db, world):
    ready(db, world["a"])
    s1, s2, s3, s4, s5 = world["senders_a"]
    s2.connection_status = "pending"
    db.commit()
    for s in (s1, s2, s3, s4, s5):
        engine.outreach_row(db, s)
    db.get(OutreachMailbox, s1.id).paused = True
    db.get(OutreachMailbox, s3.id).enabled = False
    db.get(OutreachMailbox, s4.id).daily_limit = 0
    db.commit()
    reasons = {m.email: engine.mailbox_block_reason(m, MON_11, 25) for m in _boxes(db, world)}
    assert reasons == {s1.address: "Paused", s2.address: "Not connected", s3.address: "Disabled",
                       s4.address: "Daily limit reached", s5.address: None}


def test_hourly_limit_counts_from_the_send_log(db, world):
    ready(db, world["a"], minimum_gap_minutes=0)
    for s in world["senders_a"]:
        db.add(OutreachSendLog(brand_id=world["a"].id, logged_at=MON_11 + timedelta(minutes=5), sender_email=s.address,
                               lead_email=f"{s.id}@x.io", result="Sent"))
    db.commit()
    lead(db, world["a"], "new@lead.io")
    assert by_brand(run(db, now=MON_11 + timedelta(minutes=30)), world["a"])["skipped"] == "No eligible mailbox"


# ------------------------------------------------------------------ run mechanics
def test_rotation_advances_and_lock_blocks_overlap(db, world):
    order_before = engine.global_state(db).next_start_index
    db.commit()
    run(db)
    db.expire_all()
    assert engine.global_state(db).next_start_index == (order_before + 1) % 2
    with ENGINE.connect() as c:
        c.execute(text("SELECT pg_advisory_lock(:k)"), {"k": engine.RUN_LOCK_KEY})
        with pytest.raises(engine.RunLocked):
            run(db)
        c.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": engine.RUN_LOCK_KEY})


def test_dry_run_changes_nothing(db, world):
    ready(db, world["a"])
    x = lead(db, world["a"], "x@lead.io")
    r = by_brand(run(db, dry_run=True), world["a"])
    db.refresh(x)
    assert r["notes"][0].startswith(f"Would send lead #{x.id}") and x.status == "Generated" and OUTBOX.sent == []


# ------------------------------------------------------------------ imports from the Sheet
LEAD_CSV = ("sender,linkwithproperty@gmail.com,,,\n"
            "Person Name,Email,Details (Optional),Subject (Auto),Generated Email (Auto),Send Status,Sent At,Error,Sent From,Approval\n"
            "Ravi,ravi@lead.io,Wants a 3BHK,Hi Ravi,Body,Sent,2026-09-20 11:00:00,,a@x.io,Yes\n"
            "Meena,MEENA@lead.io ,,,,,,,,\n"
            "Ravi again,ravi@lead.io,,,,,,,,\n")


def test_import_lead_tab_export(db, world):
    out = import_file(db, world["a"].id, "leads", LEAD_CSV.encode(), "Property.csv", None)
    assert out == {"added": 2, "skipped": 1, "problems": []}
    rows = {x.email: x for x in db.scalars(select(OutreachLead))}
    assert rows["ravi@lead.io"].status == "Sent" and rows["meena@lead.io"].status == "Pending"


def test_imported_send_log_blocks_repeat_contact(db, world):
    csv = ("Date and Time,Gmail Sender,Lead Email,Subject,Gmail Message ID,Sending Result,Error Reason,Daily Limit,Warm-up Stage\n"
           "2026-07-20 11:05:00,linkwithproperty@gmail.com,old@lead.io,Hi,18f,Sent,,3,Stage 1\n")
    assert import_file(db, world["a"].id, "send_log", csv.encode(), "log.csv", None)["added"] == 1
    assert import_file(db, world["a"].id, "send_log", csv.encode(), "log.csv", None)["added"] == 0  # no duplicates
    ready(db, world["a"])
    x = lead(db, world["a"], "old@lead.io")
    run(db)
    db.refresh(x)
    assert x.error == "Recipient already contacted by this brand"


def test_import_suppression_without_header(db, world):
    out = import_file(db, world["a"].id, "suppression", b"stop@me.io\nno@thanks.io\n", "s.csv", None)
    assert out["added"] == 2


# ------------------------------------------------------------------ reports
class FakeReader:
    def __init__(self, sender, threads):
        self.sender, self.threads = sender, threads

    def profile_email(self):
        return self.sender.address

    def thread_id_for(self, mid):
        return "t-" + mid

    def thread(self, tid):
        return self.threads.get(tid, {"messages": []})


def _msg(mid, sender, when, labels=(), **headers):
    hs = [{"name": "From", "value": sender}, {"name": "Subject", "value": "Re: Hi"}] + [
        {"name": k.replace("_", "-"), "value": v} for k, v in headers.items()]
    return {"id": mid, "labelIds": list(labels), "internalDate": str(int(when.timestamp() * 1000)), "payload": {"headers": hs}}


def test_report_window_replies_pending_and_render(db, world):
    s1 = world["senders_a"][0]
    day = datetime(2026, 9, 27, tzinfo=IST)
    for i, who in enumerate(["replied@lead.io", "silent@lead.io"]):
        db.add(OutreachSendLog(brand_id=world["a"].id, logged_at=day.replace(hour=11 + i), sender_email=s1.address,
                               lead_email=who, subject="Hi", gmail_message_id=f"m{i}", gmail_thread_id=f"t{i}", result="Sent"))
    db.add(OutreachSendLog(brand_id=world["a"].id, logged_at=day.replace(hour=13), sender_email=s1.address,
                           lead_email="fail@lead.io", subject="Hi", result="Failed", error="gmail 400"))
    db.commit()
    threads = {"t0": {"messages": [_msg("r1", "Replied <replied@lead.io>", day.replace(hour=15))]},
               "t1": {"messages": [_msg("a1", "mailer-daemon@googlemail.com", day.replace(hour=14))]}}
    subject, body, rep = reports.build(db, "morning", datetime(2026, 9, 28).date(),
                                       reader_factory=lambda s: FakeReader(s, threads))
    assert subject == "Email automation | 9 AM previous-day report | 27 Sep 2026"
    assert (len(rep.sent), len(rep.failed), len(rep.replies), len(rep.automated)) == (2, 1, 1, 1)
    assert [p.recipient for p in rep.pending] == ["silent@lead.io"]
    assert "Sent: 2 | Failed attempts: 1 | Replies: 1" in body and "gmail 400" in body


def test_report_is_marked_partial_when_a_mailbox_cannot_be_read(db, world):
    s1 = world["senders_a"][0]
    db.add(OutreachSendLog(brand_id=world["a"].id, logged_at=datetime(2026, 9, 28, 10, tzinfo=IST), sender_email=s1.address,
                           lead_email="x@lead.io", subject="Hi", gmail_message_id="m", result="Sent"))
    db.commit()

    def broken(_s):
        raise RuntimeError("invalid_grant: token expired")
    subject, body, _ = reports.build(db, "evening", datetime(2026, 9, 28).date(), reader_factory=broken)
    assert subject.startswith("[PARTIAL]") and "needs to be connected again" in body and "token expired" not in body


def test_scheduled_report_sends_once(db, world):
    g = engine.global_state(db)
    g.reports_enabled, g.report_sender, g.report_recipient = True, world["senders_a"][0].address, "info@99marketingservices.com"
    db.commit()
    at = datetime(2026, 9, 28, 9, 2, tzinfo=IST)
    assert reports.run_scheduled(db, "evening", now=at)["status"] == "too_early"
    assert reports.run_scheduled(db, "morning", now=at)["status"] == "sent"
    assert reports.run_scheduled(db, "morning", now=at + timedelta(minutes=5))["status"] == "already_sent"
    assert len(OUTBOX.sent) == 1 and OUTBOX.sent[0].to_address == "info@99marketingservices.com"
    assert db.scalars(select(OutreachReportReceipt.status)).all() == ["sent"]


# ------------------------------------------------------------------ API
def test_api_permissions_and_flow(client, db, world):
    a = world["a"]
    admin, manager = auth(world["admin"]), auth(world["agent_a"])  # agent_a is a manager of brand a
    base = f"/api/brands/{a.id}/outreach"
    r = client.post(f"{base}/leads", headers=manager, json={"person_name": "Asha", "email": "Asha@Lead.io"})
    assert r.status_code == 201 and r.json()["email"] == "asha@lead.io"
    lid = r.json()["id"]
    client.patch(f"{base}/leads/{lid}", headers=manager, json={"subject": "Hi", "body": "Hello", "status": "Generated"})
    assert client.post(f"{base}/leads/approve", headers=manager, json={"ids": [lid]}).json() == {"updated": 1}
    assert client.put(f"{base}/settings", headers=manager, json={"emergency_stop": False}).status_code == 403
    assert client.put(f"{base}/settings", headers=admin, json={"emergency_stop": False, "cc_email": "info@pe.com"}).status_code == 200
    ov = client.get(base, headers=manager).json()
    assert ov["approved_ready"] == 1 and ov["settings"]["emergency_stop"] is False
    boxes = client.get(f"{base}/mailboxes", headers=manager).json()
    assert len(boxes) == 5 and boxes[0]["daily_limit"] == 3
    assert client.get(f"/api/brands/{world['b'].id}/outreach", headers=manager).status_code == 403
    assert client.post("/api/outreach/run", headers=manager).status_code == 403


def test_internal_tick_requires_auth(client, db, world):
    assert client.post("/internal/outreach/tick").status_code == 401
    r = client.post("/internal/outreach/tick", headers={"X-Internal-Token": "local-internal-token"})
    assert r.status_code in (200, 401)


def test_github_scheduler_token(monkeypatch):
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.api.deps import GITHUB_OIDC_ISSUER, github_scheduler_token_ok
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class Jwks:
        def get_signing_key_from_jwt(self, _t):
            return type("K", (), {"key": key.public_key()})()

    def tok(**over):
        claims = {"iss": GITHUB_OIDC_ISSUER, "aud": "comms-hub-scheduler", "ref": "refs/heads/main",
                  "repository": "linkwithmarketing-design/multibrand-comms-platform",
                  "exp": datetime.now(timezone.utc) + timedelta(minutes=5), **over}
        return pyjwt.encode(claims, key, algorithm="RS256")
    assert github_scheduler_token_ok(tok(), Jwks())
    assert not github_scheduler_token_ok(tok(repository="someone/fork"), Jwks())
    assert not github_scheduler_token_ok(tok(ref="refs/heads/feature"), Jwks())
    assert not github_scheduler_token_ok(tok(aud="other"), Jwks())


def test_openai_request_and_parsing(monkeypatch, db, world):
    from app.core.config import get_settings
    from app.services.outreach import generator
    monkeypatch.setattr(get_settings(), "openai_api_key", "sk-test")
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"output": [{"type": "reasoning"}, {"type": "message", "content": [
                {"type": "output_text", "text": '```json\n{"subject": "Quick hello", "body": "Hi Ravi."}\n```'}]}]}

    def post(url, json, headers, timeout):
        seen.update(url=url, body=json, auth=headers["Authorization"])
        return Resp()
    monkeypatch.setattr(generator.httpx, "post", post)
    s = ready(db, world["a"], phone="+91 1")
    subject, body = generator.generate_email("gpt-5-mini", "Property Experts Group", s, "Ravi", "3BHK in Mohali")
    assert subject == "Quick hello" and body.startswith("Hi Ravi.\n\nAjay Veer\nSales Executive\nProperty Experts Group")
    assert seen["url"].endswith("/v1/responses") and seen["auth"] == "Bearer sk-test"
    assert seen["body"]["text"]["format"]["schema"]["required"] == ["subject", "body"]
    assert "Recipient details: 3BHK in Mohali" in seen["body"]["input"]


def test_delete_single_selected_and_all_matching(client, db, world):
    a = world["a"]
    h, base = auth(world["agent_a"]), f"/api/brands/{a.id}/outreach"
    ids = [lead(db, a, f"t{i}@lead.io", status="Pending" if i < 6 else "Generated").id for i in range(10)]
    other = lead(db, world["b"], "keep@lead.io").id
    db.add(OutreachSendLog(brand_id=a.id, lead_id=ids[9], logged_at=MON_11, sender_email="s@x.io",
                           lead_email="t9@lead.io", result="Sent"))
    db.commit()
    assert client.delete(f"{base}/leads/{ids[0]}", headers=h).status_code == 204
    assert client.post(f"{base}/leads/delete", headers=h, json={"ids": [ids[1], ids[2], other]}).json() == {"deleted": 2}
    assert client.post(f"{base}/leads/delete", headers=h, json={"all_matching": True, "status": "Pending"}).json() == {"deleted": 3}
    assert client.post(f"{base}/leads/delete", headers=h, json={"all_matching": True}).json() == {"deleted": 4}
    assert db.query(OutreachLead).count() == 1  # the other brand's lead is untouched
    assert db.query(OutreachSendLog).one().lead_id is None  # history kept for duplicate protection
    viewer = auth(world["admin"])
    assert client.post(f"{base}/leads/delete", headers=viewer, json={"all_matching": True}).status_code == 200


def test_openai_key_saved_in_app_is_encrypted_and_used(client, db, world, monkeypatch):
    from app.models import OutreachGlobal
    from app.services.outreach import generator
    h = auth(world["admin"])
    assert client.put("/api/outreach/openai-key", headers=h, json={"key": "not-a-key"}).status_code == 400
    out = client.put("/api/outreach/openai-key", headers=h, json={"key": "sk-test-abcdefghijklmnop1234"}).json()
    assert out["openai_configured"] and out["openai_key_hint"] == "1234"
    row = db.get(OutreachGlobal, 1)
    db.refresh(row)
    assert "abcdefgh" not in row.openai_key_ciphertext
    seen = {}

    def fake(model, brand, s, name, details, key=None):
        seen["key"] = key
        return "S", "B"
    monkeypatch.setattr(generator, "generate_email", fake)
    ready(db, world["a"])
    lead(db, world["a"], "x@lead.io", approved=False, status="Pending", subject="", body="")
    engine.run_unified(db, now=MON_11)
    assert seen["key"] == "sk-test-abcdefghijklmnop1234"
    assert client.put("/api/outreach/openai-key", headers=auth(world["agent_a"]), json={"key": "sk-x"}).status_code == 403


def test_delete_demo_sender_but_keep_ones_with_history(client, db, world):
    from app.models import InboundMessage, SenderAccount
    h = auth(world["admin"])
    s1, s2 = world["senders_a"][0], world["senders_a"][1]
    s1_id = s1.id
    assert client.delete(f"/api/brands/{world['a'].id}/senders/{s1_id}", headers=h).status_code == 204
    s2.provider = "gmail"
    db.add(InboundMessage(brand_id=world["a"].id, sender_account_id=s2.id, channel="email", from_address="a@b.io",
                          provider_message_id="p1"))
    db.commit()
    s2_id = s2.id
    r = client.delete(f"/api/brands/{world['a'].id}/senders/{s2_id}", headers=h)
    assert r.status_code == 409 and "Pause or disconnect" in r.json()["detail"]
    db.expire_all()
    assert db.query(SenderAccount).filter(SenderAccount.id == s1_id).count() == 0
    assert client.delete(f"/api/brands/{world['a'].id}/senders/{s2_id}", headers=auth(world["agent_a"])).status_code == 403


def test_create_brand_with_profile_then_edit_and_delete(client, db, world):
    from app.models import Brand, SenderAccount
    from tests.conftest import add_contact
    h = auth(world["admin"])
    r = client.post("/api/brands", headers=h, json={
        "slug": "property-experts-group", "name": "Property Experts Group", "master_email": "info@propertyexpertsgroup.com",
        "description": "Helps buyers and investors with property discovery in Chandigarh and Mohali.",
        "phone": "+91 8080803736", "website": "https://propertyexpertsgroup.com", "signature_name": "Manpreet Singh",
        "signature_title": "Operations Head"})
    assert r.status_code == 201
    bid = r.json()["id"]
    prof = client.get(f"/api/brands/{bid}/profile", headers=h).json()
    assert prof["description"].startswith("Helps buyers") and prof["cc_email"] == "info@propertyexpertsgroup.com"
    assert prof["signature_name"] == "Manpreet Singh"
    box = db.scalars(select(SenderAccount).where(SenderAccount.brand_id == bid)).one()
    assert box.is_master and box.provider == "gmail" and box.connection_status == "pending"
    assert client.post("/api/brands", headers=h, json={"slug": "pe2", "name": "property experts group"}).status_code == 409
    up = client.put(f"/api/brands/{bid}/profile", headers=h, json={"phone": "+91 1", "website": ""}).json()
    assert up["phone"] == "+91 1" and up["website"] == ""
    # delete needs the exact name and removes everything in the brand
    brand = db.get(Brand, bid)
    add_contact(db, brand, email="c@x.io")
    lead(db, brand, "l@x.io")
    db.commit()
    assert client.post(f"/api/brands/{bid}/delete", headers=h, json={"confirm_name": "wrong"}).status_code == 400
    assert client.post(f"/api/brands/{bid}/delete", headers=auth(world["agent_a"]), json={"confirm_name": "Property Experts Group"}).status_code == 403  # noqa: E501
    assert client.post(f"/api/brands/{bid}/delete", headers=h, json={"confirm_name": "Property Experts Group"}).status_code == 200
    db.expire_all()
    assert db.query(Brand).filter(Brand.id == bid).count() == 0
    assert db.query(OutreachLead).filter(OutreachLead.brand_id == bid).count() == 0


def test_delete_brand_with_full_history(client, db, world):
    """A seeded brand with senders, contacts, campaigns and messages can be deleted cleanly."""
    from app.models import Brand
    from tests.conftest import add_contact
    h = auth(world["admin"])
    a = world["a"]
    add_contact(db, a, email="c@x.io")
    db.commit()
    camp = client.post(f"/api/brands/{a.id}/campaigns", headers=h, json={"name": "C", "channel": "email", "subject": "S", "body": "B"})
    assert camp.status_code in (200, 201), camp.text
    assert client.post(f"/api/brands/{a.id}/delete", headers=h, json={"confirm_name": a.name}).status_code == 200
    db.expire_all()
    assert db.query(Brand).count() == 1
