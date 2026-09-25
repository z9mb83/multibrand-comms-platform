import threading
from collections import Counter

from sqlalchemy import select

from app.models import OutboundMessage, SenderAssignment, SenderRoutingState
from app.providers.mock import OUTBOX
from app.services.dispatch import create_message, process_outbound
from app.services.routing import assign_sender
from tests.conftest import add_contact


def _send(db, factory, brand, bc, **kw):
    msg = create_message(db, brand_id=brand.id, contact_id=bc.contact_id, channel="email", to_address=bc.email,
                         subject="Hi", body="Hello", status="queued", **kw)
    db.commit()
    outcome = process_outbound(factory, msg.id)
    db.expire_all()
    return db.get(OutboundMessage, msg.id), outcome


def test_auto_round_robin_wraps_in_order(db, factory, world):
    a, senders = world["a"], world["senders_a"]
    picked = []
    for i in range(6):
        bc = add_contact(db, a, email=f"c{i}@x.test")
        msg, outcome = _send(db, factory, a, bc)
        assert outcome == "sent"
        picked.append(msg.sender_account_id)
    assert picked == [s.id for s in senders] + [senders[0].id]
    state = db.scalars(select(SenderRoutingState).where(SenderRoutingState.brand_id == a.id)).one()
    assert state.last_sender_id == senders[0].id and state.assignments_total == 6


def test_round_robin_is_independent_per_brand(db, world):
    a, b = world["a"], world["b"]
    first_a = assign_sender(db, brand_id=a.id, channel="email").sender
    first_b = assign_sender(db, brand_id=b.id, channel="email").sender
    second_a = assign_sender(db, brand_id=a.id, channel="email").sender
    db.commit()
    assert first_a.brand_id == a.id and second_a.brand_id == a.id and first_b.brand_id == b.id
    assert first_a.id == world["senders_a"][0].id and second_a.id == world["senders_a"][1].id
    assert first_b.id == world["senders_b"][0].id


def test_skips_ineligible_accounts_and_never_exceeds_limits(db, world):
    a, s = world["a"], world["senders_a"]
    s[1].status = "paused"
    s[2].connection_status = "error"
    s[3].daily_limit = 1
    s[4].provider_daily_limit = 0  # provider-authorised limit exhausted
    db.commit()
    got = [assign_sender(db, brand_id=a.id, channel="email").sender for _ in range(3)]
    db.commit()
    ids = [g.id if g else None for g in got]
    # s0 and s3 eligible; s3 only once (daily_limit=1)
    assert ids == [s[0].id, s[3].id, s[0].id]
    s[0].daily_limit = 3  # already used twice -> one more then nothing
    db.commit()
    assert assign_sender(db, brand_id=a.id, channel="email").sender.id == s[0].id
    d = assign_sender(db, brand_id=a.id, channel="email")
    db.commit()
    assert d.sender is None and d.outcome == "no_eligible"


def test_no_eligible_sender_keeps_message_waiting(db, factory, world):
    a = world["a"]
    for s in world["senders_a"]:
        s.status = "paused"
    bc = add_contact(db, a, email="wait@x.test")
    db.commit()
    msg, outcome = _send(db, factory, a, bc)
    assert outcome == "waiting_for_sender"
    assert msg.status == "queued" and msg.hold_reason.startswith("no_eligible_sender")
    assert OUTBOX.sent == []


def test_concurrent_assignment_is_conflict_free(factory, world):
    brand_id = world["a"].id
    n_threads, per_thread = 10, 10
    results: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()
        for _ in range(per_thread):
            with factory() as s, s.begin():
                d = assign_sender(s, brand_id=brand_id, channel="email")
                with lock:
                    results.append(d.sender.id)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    counts = Counter(results)
    assert len(results) == 100
    assert sorted(counts.values()) == [20, 20, 20, 20, 20]  # perfectly even => no two workers saw the same cursor
    with factory() as s:
        state = s.scalars(select(SenderRoutingState).where(SenderRoutingState.brand_id == brand_id)).one()
        assert state.assignments_total == 100
        logged = s.scalars(select(SenderAssignment).where(SenderAssignment.outcome == "assigned")).all()
        assert len(logged) == 100
        # the assignment log, in commit order, is a strict rotation
        order = [x.sender_account_id for x in sorted(logged, key=lambda x: x.id)]
        ids = [x.id for x in world["senders_a"]]
        assert order == [ids[i % 5] for i in range(100)]


def test_fixed_sender_used_exactly(db, factory, world):
    a, s = world["a"], world["senders_a"]
    bc = add_contact(db, a, email="fixed@x.test")
    msg, outcome = _send(db, factory, a, bc, routing_mode="FIXED", requested_sender_id=s[3].id)
    assert outcome == "sent" and msg.sender_account_id == s[3].id


def test_fixed_sender_unavailable_is_held_not_substituted(db, factory, world):
    a, s = world["a"], world["senders_a"]
    s[2].status = "paused"
    bc = add_contact(db, a, email="held@x.test")
    db.commit()
    msg, outcome = _send(db, factory, a, bc, routing_mode="FIXED", requested_sender_id=s[2].id)
    assert outcome == "held"
    assert msg.status == "held_for_review" and msg.sender_account_id is None
    assert "paused" in msg.hold_reason
    assert OUTBOX.sent == []


def test_fixed_sender_from_other_brand_rejected(db, world):
    import pytest

    from app.services.common import DomainError
    a, b = world["a"], world["senders_b"]
    bc = add_contact(db, a, email="x@x.test")
    with pytest.raises(DomainError):
        create_message(db, brand_id=a.id, contact_id=bc.contact_id, channel="email", to_address=bc.email,
                       routing_mode="FIXED", requested_sender_id=b[0].id)


def test_sticky_sender_is_preserved_for_contact(db, factory, world):
    a = world["a"]
    bc = add_contact(db, a, email="sticky@x.test")
    other = add_contact(db, a, email="other@x.test")
    db.commit()
    first, _ = _send(db, factory, a, bc, routing_mode="STICKY")
    _send(db, factory, a, other)  # advances the round-robin cursor
    # close the thread so continuity doesn't apply; STICKY must still reuse the contact's sender
    from app.models import Conversation
    conv = db.get(Conversation, first.conversation_id)
    conv.status = "closed"
    db.commit()
    second, _ = _send(db, factory, a, bc, routing_mode="STICKY")
    assert second.sender_account_id == first.sender_account_id


def test_email_thread_continuity_uses_original_sender_and_headers(db, factory, world):
    a = world["a"]
    bc = add_contact(db, a, email="thread@x.test")
    db.commit()
    first, _ = _send(db, factory, a, bc)
    for i in range(3):  # move the cursor well past the original sender
        _send(db, factory, a, add_contact(db, a, email=f"n{i}@x.test"))
    follow, _ = _send(db, factory, a, bc)  # AUTO requested, but an open thread exists
    assert follow.sender_account_id == first.sender_account_id
    assert follow.conversation_id == first.conversation_id
    req = OUTBOX.sent[-1]
    assert req.thread_id == first.provider_thread_id
    assert req.in_reply_to == f"<{first.provider_message_id}@mock.local>"
