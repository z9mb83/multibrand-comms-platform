import threading
from datetime import timedelta

from sqlalchemy import select

from app.models import (
    Campaign,
    Conversation,
    DailyTask,
    FollowUpRule,
    FollowUpTask,
    InboundMessage,
    MessageAttempt,
    OutboundMessage,
    Suppression,
)
from app.providers.mock import OUTBOX
from app.services import campaigns, followups
from app.services.common import now_utc
from app.services.contacts import get_consent, set_consent
from app.services.dispatch import create_message, process_outbound
from app.services.inbound import InboundEvent, ingest
from tests.conftest import add_contact


def queued(db, brand, bc, **kw):
    m = create_message(db, brand_id=brand.id, contact_id=bc.contact_id, channel=kw.pop("channel", "email"),
                       to_address=kw.pop("to", bc.email), subject="Hello", body="Body", status="queued", **kw)
    db.commit()
    return m


# ---------- consent enforcement
def test_message_without_consent_is_suppressed(db, factory, world):
    a = world["a"]
    bc = add_contact(db, a, email="nocons@x.test", consent=())
    m = queued(db, a, bc)
    assert process_outbound(factory, m.id) == "suppressed"
    db.expire_all()
    assert db.get(OutboundMessage, m.id).hold_reason == "no_email_consent"
    assert OUTBOX.sent == []


def test_consent_is_per_channel_and_per_brand(db, factory, world):
    a, b = world["a"], world["b"]
    bc_a = add_contact(db, a, email="p@x.test", phone="+919812345678", consent=("email",))
    bc_b = add_contact(db, b, email="p@x.test", consent=())  # same person, other brand, no consent
    wa = queued(db, a, bc_a, channel="whatsapp", to=bc_a.phone)
    mb = queued(db, b, bc_b)
    assert process_outbound(factory, wa.id) == "suppressed"  # email consent doesn't cover WhatsApp
    assert process_outbound(factory, mb.id) == "suppressed"  # brand A consent doesn't cover brand B
    ma = queued(db, a, bc_a)
    assert process_outbound(factory, ma.id) == "sent"


# ---------- duplicate-send prevention
def test_repeated_dispatch_sends_once(db, factory, world):
    a = world["a"]
    bc = add_contact(db, a, email="once@x.test")
    m = queued(db, a, bc, idempotency_key="k-1")
    assert process_outbound(factory, m.id) == "sent"
    assert process_outbound(factory, m.id) == "already_done"
    again = create_message(db, brand_id=a.id, contact_id=bc.contact_id, channel="email", to_address=bc.email,
                           status="queued", idempotency_key="k-1")
    assert again.id == m.id
    assert len(OUTBOX.sent) == 1
    assert db.query(MessageAttempt).count() == 1


def test_concurrent_dispatch_of_same_message_sends_once(db, factory, world):
    a = world["a"]
    bc = add_contact(db, a, email="race@x.test")
    m = queued(db, a, bc)
    outcomes = []
    barrier = threading.Barrier(8)

    def go():
        barrier.wait()
        outcomes.append(process_outbound(factory, m.id))

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert outcomes.count("sent") == 1
    assert len(OUTBOX.sent) == 1


def test_expired_sending_lease_is_not_resent(db, factory, world):
    a = world["a"]
    bc = add_contact(db, a, email="lease@x.test")
    m = queued(db, a, bc)
    m.status, m.sending_started_at = "sending", now_utc() - timedelta(hours=1)
    db.commit()
    assert process_outbound(factory, m.id) == "unknown"
    assert OUTBOX.sent == []


def test_transient_failure_retries_then_sends_once(db, factory, world):
    a = world["a"]
    bc = add_contact(db, a, email="retry@x.test")
    m = queued(db, a, bc)
    OUTBOX.fail_next = 1
    assert process_outbound(factory, m.id) == "retry_scheduled"
    db.expire_all()
    msg = db.get(OutboundMessage, m.id)
    msg.scheduled_at = now_utc() - timedelta(seconds=1)
    db.commit()
    assert process_outbound(factory, m.id) == "sent"
    assert len(OUTBOX.sent) == 1 and db.query(MessageAttempt).count() == 2


def test_campaign_pause_and_emergency_stop_block_sending(db, factory, world):
    a, admin = world["a"], world["admin"]
    c = Campaign(brand_id=a.id, name="C", channel="email", status="draft", allowed_sender_ids=[], segment={})
    db.add(c)
    db.flush()
    contacts = [add_contact(db, a, email=f"cp{i}@x.test") for i in range(3)]
    for bc in contacts:
        create_message(db, brand_id=a.id, contact_id=bc.contact_id, channel="email", to_address=bc.email,
                       subject="S", body="B", campaign_id=c.id)
    db.commit()
    campaigns.transition(db, c, "approve", admin.id)
    ids = campaigns.transition(db, c, "launch", admin.id)
    campaigns.transition(db, c, "pause", admin.id)
    db.commit()
    assert process_outbound(factory, ids[0]) == "campaign_paused"
    campaigns.transition(db, c, "emergency_stop", admin.id, "test")
    db.commit()
    assert process_outbound(factory, ids[1]) == "already_done"
    assert OUTBOX.sent == []
    db.expire_all()
    assert {m.status for m in db.scalars(select(OutboundMessage))} == {"cancelled"}


# ---------- inbound matching
def _sent_campaign_message(db, factory, world, email="lead@x.test", rule_steps=None):
    a, admin = world["a"], world["admin"]
    c = Campaign(brand_id=a.id, name="Outreach", channel="email", status="draft", allowed_sender_ids=[], segment={})
    db.add(c)
    db.flush()
    if rule_steps is not None:
        db.add(FollowUpRule(brand_id=a.id, campaign_id=c.id, name="FU", steps=rule_steps, max_attempts=len(rule_steps)))
    bc = add_contact(db, a, email=email)
    create_message(db, brand_id=a.id, contact_id=bc.contact_id, channel="email", to_address=email, subject="Offer",
                   body="Hi", campaign_id=c.id)
    db.commit()
    campaigns.transition(db, c, "approve", admin.id)
    ids = campaigns.transition(db, c, "launch", admin.id)
    db.commit()
    assert process_outbound(factory, ids[0]) == "sent"
    db.expire_all()
    return db.get(OutboundMessage, ids[0]), c, bc


def test_reply_matched_to_brand_sender_contact_conversation_campaign(db, factory, world):
    out, camp, bc = _sent_campaign_message(db, factory, world)
    # the same address is also a contact of brand B — must NOT influence matching
    add_contact(db, world["b"], email="lead@x.test")
    db.commit()
    sender = world["senders_a"][0]
    msg = ingest(db, InboundEvent(channel="email", to_address=sender.address.upper(), from_address="Lead@X.test",
                                  provider_message_id="in-1", body="Sounds good, I'm interested",
                                  thread_id=out.provider_thread_id))
    db.commit()
    assert msg.brand_id == world["a"].id
    assert msg.sender_account_id == sender.id
    assert msg.contact_id == bc.contact_id
    assert msg.conversation_id == out.conversation_id
    assert msg.campaign_id == camp.id
    assert msg.classification == "Interested"
    conv = db.get(Conversation, out.conversation_id)
    assert conv.status == "needs_reply" and conv.unread_count == 1
    assert db.scalars(select(DailyTask).where(DailyTask.conversation_id == conv.id)).first() is not None
    # webhook retry is idempotent
    again = ingest(db, InboundEvent(channel="email", to_address=sender.address, from_address="lead@x.test",
                                    provider_message_id="in-1", body="dup"))
    assert again.id == msg.id


def test_reply_to_other_brand_mailbox_stays_in_that_brand(db, factory, world):
    _sent_campaign_message(db, factory, world)
    b_sender = world["senders_b"][2]
    msg = ingest(db, InboundEvent(channel="email", to_address=b_sender.address, from_address="lead@x.test",
                                  provider_message_id="in-b", body="Hello B"))
    db.commit()
    assert msg.brand_id == world["b"].id  # identified by receiving mailbox, not by the contact's email
    conv = db.get(Conversation, msg.conversation_id)
    assert conv.brand_id == world["b"].id and conv.sender_account_id == b_sender.id


def test_human_can_override_classification(db, factory, world):
    from app.services.inbound import set_classification
    _sent_campaign_message(db, factory, world)
    msg = ingest(db, InboundEvent(channel="email", to_address=world["senders_a"][0].address,
                                  from_address="lead@x.test", provider_message_id="in-c", body="hmm"))
    set_classification(db, msg.id, "Callback requested", world["admin"].id)
    db.commit()
    db.expire_all()
    m = db.get(InboundMessage, msg.id)
    assert m.classification == "Callback requested" and m.classification_source == "human"


# ---------- follow-ups
def test_followup_schedule_and_cancellation_on_reply(db, factory, world):
    out, camp, bc = _sent_campaign_message(db, factory, world, rule_steps=[{"after_days": 2}, {"after_days": 6}])
    fu = db.scalars(select(FollowUpTask)).one()
    assert fu.step_index == 0 and fu.status == "pending"
    assert abs((fu.due_at - out.sent_at) - timedelta(days=2)) < timedelta(seconds=5)

    # Day 3: first follow-up task created, second step scheduled for Day 7
    stats = followups.run_sweep(db, now=out.sent_at + timedelta(days=2, hours=1))
    db.commit()
    assert stats["tasks_created"] == 1
    step2 = db.scalars(select(FollowUpTask).where(FollowUpTask.step_index == 1)).one()
    assert abs((step2.due_at - out.sent_at) - timedelta(days=6)) < timedelta(seconds=5)

    # Customer replies before Day 7 -> pending follow-up pauses immediately
    ingest(db, InboundEvent(channel="email", to_address=world["senders_a"][0].address, from_address=bc.email,
                            provider_message_id="r1", body="Please share details", thread_id=out.provider_thread_id))
    db.commit()
    db.expire_all()
    assert db.get(FollowUpTask, step2.id).status == "paused_on_reply"
    stats = followups.run_sweep(db, now=out.sent_at + timedelta(days=7))
    db.commit()
    assert stats["tasks_created"] == 0
    assert db.query(DailyTask).filter(DailyTask.source == "follow_up").count() == 1

    # human reviews and resumes
    followups.resume(db, out.conversation_id, world["admin"].id)
    db.commit()
    assert db.get(FollowUpTask, step2.id).status == "pending"


def test_followup_max_attempts_and_approval_flow(db, factory, world):
    out, camp, bc = _sent_campaign_message(db, factory, world, rule_steps=[
        {"after_days": 2, "mode": "approval_send"}, {"after_days": 6, "mode": "approval_send"},
        {"after_days": 9, "mode": "approval_send"}])
    rule = db.scalars(select(FollowUpRule)).one()
    rule.max_attempts = 2
    db.commit()
    followups.run_sweep(db, now=out.sent_at + timedelta(days=2, hours=1))
    db.commit()
    t1 = db.scalars(select(FollowUpTask).where(FollowUpTask.step_index == 0)).one()
    assert t1.status == "awaiting_approval"
    msg = followups.approve_and_send(db, t1.id, world["admin"].id, body="Just following up")
    db.commit()
    assert process_outbound(factory, msg.id) == "sent"
    db.expire_all()
    fu_msg = db.get(OutboundMessage, msg.id)
    assert fu_msg.sender_account_id == out.sender_account_id  # original sender maintained
    assert fu_msg.conversation_id == out.conversation_id
    followups.run_sweep(db, now=out.sent_at + timedelta(days=6, hours=1))
    db.commit()
    # step index 2 would exceed max_attempts=2 -> never created
    assert db.scalars(select(FollowUpTask).where(FollowUpTask.step_index == 2)).first() is None


# ---------- unsubscribe
def test_unsubscribe_reply_suppresses_only_that_brand(db, factory, world):
    out, camp, bc = _sent_campaign_message(db, factory, world, rule_steps=[{"after_days": 2}])
    bc_b = add_contact(db, world["b"], email=bc.email)
    db.commit()
    msg = ingest(db, InboundEvent(channel="email", to_address=world["senders_a"][0].address, from_address=bc.email,
                                  provider_message_id="u1", body="STOP"))
    db.commit()
    assert msg.is_unsubscribe and msg.classification == "Unsubscribe"
    assert db.scalars(select(Suppression).where(Suppression.brand_id == world["a"].id)).one().address == bc.email
    assert get_consent(db, world["a"].id, bc.contact_id, "email") == "withdrawn"
    assert db.scalars(select(FollowUpTask)).one().status == "cancelled"
    # further brand A sends are suppressed even if consent is re-granted by mistake
    set_consent(db, world["a"].id, bc.contact_id, "email", True, "oops")
    m2 = queued(db, world["a"], bc)
    assert process_outbound(factory, m2.id) == "suppressed"
    # brand B unaffected
    m3 = queued(db, world["b"], bc_b)
    assert process_outbound(factory, m3.id) == "sent"
