from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import DailyTask, MasterReport, NotificationDelivery, SenderAccount
from app.providers.mock import OUTBOX
from app.services import reports, tasks
from app.services.dispatch import create_message, process_outbound
from app.services.inbound import InboundEvent, ingest
from tests.conftest import add_contact

IST = ZoneInfo("Asia/Kolkata")


def configure(db, world, channel="email"):
    notif = SenderAccount(brand_id=world["a"].id, channel="email", address="reports@hq.test", provider="mock",
                          status="active", connection_status="connected", is_notification_sender=True,
                          daily_limit=100, hourly_limit=100, provider_config={})
    wa = SenderAccount(brand_id=world["a"].id, channel="whatsapp", address="+919999900000", provider="mock",
                       status="active", connection_status="connected", is_notification_sender=True,
                       daily_limit=100, hourly_limit=100, provider_config={})
    db.add_all([notif, wa])
    db.flush()
    s = reports.get_settings_row(db)
    s.master_email, s.master_mobile, s.notification_channel = "owner@hq.test", "+919811111111", channel
    s.notification_email_sender_id, s.notification_whatsapp_sender_id = notif.id, wa.id
    s.mobile_opt_in_at = datetime.now(IST)
    db.commit()
    return s


def test_daily_report_content_per_brand(db, factory, world):
    a, b, admin = world["a"], world["b"], world["admin"]
    configure(db, world)
    for i in range(3):
        bc = add_contact(db, a, email=f"r{i}@x.test")
        m = create_message(db, brand_id=a.id, contact_id=bc.contact_id, channel="email", to_address=bc.email,
                           subject="s", body="b", status="queued")
        db.commit()
        process_outbound(factory, m.id)
    bcb = add_contact(db, b, email="rb@x.test")
    mb = create_message(db, brand_id=b.id, contact_id=bcb.contact_id, channel="email", to_address=bcb.email,
                        subject="s", body="b", status="queued")
    db.commit()
    process_outbound(factory, mb.id)
    ingest(db, InboundEvent(channel="email", to_address=world["senders_a"][0].address, from_address="r0@x.test",
                            provider_message_id="x1", body="call me"))
    t = DailyTask(brand_id=a.id, description="Call back r0", status="Pending", assigned_user_id=admin.id,
                  due_at=datetime.now(IST).replace(hour=23, minute=0))
    db.add(t)
    db.flush()
    done = DailyTask(brand_id=a.id, description="Send brochure", status="Pending")
    db.add(done)
    db.flush()
    tasks.update_status(db, done, "Completed", admin.id)
    db.commit()

    today = datetime.now(IST).date()
    data = reports.build(db, "evening", today)
    by = {x["brand"]: x for x in data["brands"]}
    assert by["Marketing Co"]["messages_sent"] == 3 and by["Staffing Co"]["messages_sent"] == 1
    assert by["Marketing Co"]["sent_by_channel"]["email"] == 3
    assert by["Marketing Co"]["new_replies"] == 1 and by["Marketing Co"]["unanswered_conversations"] == 1
    assert [x["description"] for x in by["Marketing Co"]["completed_tasks"]] == ["Send brochure"]
    assert data["overall"]["messages_sent"] == 4
    assert any("await a reply" in a for a in data["action_items"])
    morning = reports.build(db, "morning", today)
    sched = {x["brand"]: x for x in morning["brands"]}["Marketing Co"]["scheduled_tasks"]
    assert any(s["description"] == "Call back r0" and s["owner"] == "Super Admin" for s in sched)
    html = reports.render_html(data)
    assert "Marketing Co" in html and "<table" in html
    assert len(reports.render_mobile(data)) <= 1000


def test_brand_can_be_excluded_via_preferences(db, world):
    s = configure(db, world)
    s.brand_preferences = {str(world["b"].id): {"include": False}}
    db.commit()
    data = reports.build(db, "morning", datetime.now(IST).date())
    assert [x["brand"] for x in data["brands"]] == ["Marketing Co"]


def test_scheduler_run_is_idempotent_and_respects_time(db, world):
    configure(db, world, channel="email+whatsapp")
    monday_0900 = datetime(2026, 9, 28, 9, 0, tzinfo=IST)
    assert reports.run_due(db, now=monday_0900) == []  # before 09:30
    made = reports.run_due(db, now=monday_0900 + timedelta(minutes=35))
    db.commit()
    assert [r.kind for r in made] == ["morning"]
    assert reports.run_due(db, now=monday_0900 + timedelta(minutes=50)) == []  # already generated
    sunday = datetime(2026, 9, 27, 10, 0, tzinfo=IST)
    assert reports.run_due(db, now=sunday) == []  # not a working day
    deliveries = db.scalars(select(NotificationDelivery)).all()
    assert sorted(d.channel for d in deliveries) == ["email", "whatsapp"]


def test_report_delivery_retries_safely(db, world):
    configure(db, world)
    report = reports.generate(db, "morning", datetime.now(IST).date())
    db.commit()
    OUTBOX.fail_next = 2
    now = datetime.now(IST)
    assert reports.deliver_pending(db, now=now)["retrying"] == 1
    d = db.scalars(select(NotificationDelivery)).one()
    assert d.status == "retrying" and d.attempts == 1 and d.next_attempt_at > now
    assert reports.deliver_pending(db, now=now)["sent"] == 0  # not due yet: no hammering
    reports.deliver_pending(db, now=d.next_attempt_at + timedelta(seconds=1))
    reports.deliver_pending(db, now=now + timedelta(hours=1))
    db.commit()
    db.expire_all()
    d = db.scalars(select(NotificationDelivery)).one()
    assert d.status == "sent" and d.attempts == 3
    assert db.get(MasterReport, report.id).status == "delivered"
    assert len(OUTBOX.sent) == 1 and OUTBOX.sent[0].to_address == "owner@hq.test"
    # generating the same report again does not duplicate deliveries
    reports.generate(db, "morning", report.report_date, force=True)
    db.commit()
    assert db.query(NotificationDelivery).count() == 1


def test_report_delivery_gives_up_and_records_failure(db, world):
    configure(db, world)
    reports.generate(db, "evening", datetime.now(IST).date())
    OUTBOX.fail_next, OUTBOX.fail_retryable = 1, False
    reports.deliver_pending(db, now=datetime.now(IST))
    db.commit()
    d = db.scalars(select(NotificationDelivery)).one()
    assert d.status == "failed" and "mock" in d.last_error
    assert db.scalars(select(MasterReport)).one().status == "failed"


def test_reports_use_notification_sender_not_marketing_rotation(db, world):
    configure(db, world)
    reports.generate(db, "morning", datetime.now(IST).date())
    reports.deliver_pending(db)
    db.commit()
    assert OUTBOX.sent[0].from_address == "reports@hq.test"
    from app.models import SenderRoutingState
    assert db.query(SenderRoutingState).count() == 0


def test_task_completion_requires_user_and_carry_forward(db, world):
    import pytest

    from app.services.common import DomainError
    t = DailyTask(brand_id=world["a"].id, description="x", status="Pending",
                  due_at=datetime(2026, 9, 25, 15, 0, tzinfo=IST))
    db.add(t)
    db.flush()
    with pytest.raises(DomainError):
        tasks.update_status(db, t, "Completed", None)
    n = tasks.carry_forward(db, now=datetime(2026, 9, 27, 8, 0, tzinfo=IST))  # Sunday
    db.commit()
    assert n == 1
    assert t.due_at.astimezone(IST).date().isoformat() == "2026-09-28" and t.carry_count == 1
    assert tasks.mark_overdue(db, now=datetime(2026, 9, 28, 16, 0, tzinfo=IST)) == 1
