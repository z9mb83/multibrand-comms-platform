"""Intelligent sender routing: AUTO (round-robin) / FIXED / STICKY, plus thread continuity.

Concurrency: every assignment for a (brand, channel) first locks that pair's row in
`sender_routing_state` with SELECT ... FOR UPDATE. All modes take the same lock so the
cursor and the per-account usage counters are updated atomically; concurrent workers
serialise on the lock and can never pick from a stale cursor.

Compliance: an account is only eligible when connected, active, not paused, within the
configured AND provider limits and allowed for the campaign. When no account is eligible
the message waits; accounts are never rotated to get around a limit.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import BrandContact, Campaign, Conversation, SenderAccount, SenderAssignment, SenderRoutingState
from app.models.enums import ConnectionStatus, RoutingMode, SenderStatus
from app.services.common import local_tz, now_utc


@dataclass
class RoutingDecision:
    sender: SenderAccount | None
    outcome: str  # assigned | held | no_eligible
    mode: str
    reason: str | None = None


STICKY_FIELD = {"email": "sticky_email_sender_id", "whatsapp": "sticky_whatsapp_sender_id", "sms": "sticky_sms_sender_id"}


def _roll_counters(acc: SenderAccount, now: datetime) -> None:
    today = now.astimezone(local_tz()).date()
    hour = now.replace(minute=0, second=0, microsecond=0)
    if acc.usage_date != today:
        acc.usage_date, acc.sent_today = today, 0
    if acc.usage_hour is None or acc.usage_hour != hour:
        acc.usage_hour, acc.sent_this_hour = hour, 0


def effective_daily_limit(acc: SenderAccount) -> int:
    return min(acc.daily_limit, acc.provider_daily_limit) if acc.provider_daily_limit is not None else acc.daily_limit


def ineligibility_reason(acc: SenderAccount, campaign: Campaign | None = None, now: datetime | None = None) -> str | None:
    now = now or now_utc()
    if acc.connection_status != ConnectionStatus.CONNECTED:
        return f"not_connected:{acc.connection_status}"
    if acc.status == SenderStatus.PAUSED:
        return "paused"
    if acc.status != SenderStatus.ACTIVE:
        return f"not_active:{acc.status}"
    _roll_counters(acc, now)
    if acc.sent_today >= effective_daily_limit(acc):
        return "daily_limit_reached"
    if acc.sent_this_hour >= acc.hourly_limit:
        return "hourly_limit_reached"
    if campaign is not None and campaign.allowed_sender_ids and acc.id not in campaign.allowed_sender_ids:
        return "not_allowed_for_campaign"
    return None


def _lock_state(db: Session, brand_id: int, channel: str) -> SenderRoutingState:
    db.execute(pg_insert(SenderRoutingState).values(brand_id=brand_id, channel=channel, assignments_total=0)
               .on_conflict_do_nothing(index_elements=["brand_id", "channel"]))
    return db.scalars(select(SenderRoutingState)
                      .where(SenderRoutingState.brand_id == brand_id, SenderRoutingState.channel == channel)
                      .with_for_update()).one()


def _candidates(db: Session, brand_id: int, channel: str) -> list[SenderAccount]:
    return list(db.scalars(select(SenderAccount)
                           .where(SenderAccount.brand_id == brand_id, SenderAccount.channel == channel,
                                  SenderAccount.is_notification_sender.is_(False))
                           .order_by(SenderAccount.rotation_order, SenderAccount.id)
                           .with_for_update()))


def _record(db: Session, brand_id: int, channel: str, mode: str, outcome: str, sender_id: int | None,
            reason: str | None, message_id: int | None) -> None:
    db.add(SenderAssignment(brand_id=brand_id, channel=channel, routing_mode=mode, outcome=outcome,
                            sender_account_id=sender_id, reason=reason, outbound_message_id=message_id))


def _consume(acc: SenderAccount, state: SenderRoutingState, advance_cursor: bool) -> None:
    acc.sent_today += 1
    acc.sent_this_hour += 1
    state.assignments_total += 1
    if advance_cursor:
        state.last_sender_id = acc.id


def next_auto_sender(accounts: list[SenderAccount], last_sender_id: int | None, campaign: Campaign | None,
                     now: datetime | None = None) -> tuple[SenderAccount | None, dict[int, str]]:
    """Pure round-robin selection: first eligible account after the cursor, wrapping once."""
    if not accounts:
        return None, {}
    start = 0
    if last_sender_id is not None:
        ids = [a.id for a in accounts]
        if last_sender_id in ids:
            start = ids.index(last_sender_id) + 1
        else:
            # cursor account removed: continue from the next higher id
            start = next((i for i, a in enumerate(accounts) if a.id > last_sender_id), 0)
    rejected: dict[int, str] = {}
    n = len(accounts)
    for i in range(n):
        acc = accounts[(start + i) % n]
        reason = ineligibility_reason(acc, campaign, now)
        if reason is None:
            return acc, rejected
        rejected[acc.id] = reason
    return None, rejected


def assign_sender(db: Session, *, brand_id: int, channel: str, mode: str = RoutingMode.AUTO,
                  fixed_sender_id: int | None = None, brand_contact: BrandContact | None = None,
                  conversation: Conversation | None = None, campaign: Campaign | None = None,
                  message_id: int | None = None) -> RoutingDecision:
    """Must be called inside a transaction; the caller commits."""
    now = now_utc()
    state = _lock_state(db, brand_id, channel)

    def fixed(acc_id: int, used_mode: str, why: str) -> RoutingDecision:
        acc = db.scalars(select(SenderAccount).where(SenderAccount.id == acc_id).with_for_update()).first()
        if acc is None or acc.brand_id != brand_id or acc.channel != channel:
            reason = f"{why}:sender_not_in_brand"
            _record(db, brand_id, channel, used_mode, "held", acc_id, reason, message_id)
            return RoutingDecision(None, "held", used_mode, reason)
        reason = ineligibility_reason(acc, campaign, now)
        if reason:
            reason = f"{why}:{reason}"
            _record(db, brand_id, channel, used_mode, "held", acc.id, reason, message_id)
            return RoutingDecision(None, "held", used_mode, reason)
        _consume(acc, state, advance_cursor=False)
        _record(db, brand_id, channel, used_mode, "assigned", acc.id, why, message_id)
        return RoutingDecision(acc, "assigned", used_mode, why)

    # 1. Ongoing thread keeps its original sender (email replies must come from the same mailbox)
    if conversation is not None and conversation.sender_account_id and conversation.status != "closed":
        return fixed(conversation.sender_account_id, RoutingMode.STICKY, "thread_continuity")

    # 2. FIXED: exact sender or hold for review; never substitute
    if mode == RoutingMode.FIXED:
        if not fixed_sender_id:
            _record(db, brand_id, channel, mode, "held", None, "fixed_sender_missing", message_id)
            return RoutingDecision(None, "held", mode, "fixed_sender_missing")
        return fixed(fixed_sender_id, RoutingMode.FIXED, "fixed")

    # 3. STICKY: previously assigned sender for this contact
    if mode == RoutingMode.STICKY and brand_contact is not None:
        sticky_id = getattr(brand_contact, STICKY_FIELD[channel])
        if sticky_id:
            return fixed(sticky_id, RoutingMode.STICKY, "sticky_contact")

    # 4. AUTO round-robin
    accounts = _candidates(db, brand_id, channel)
    chosen, rejected = next_auto_sender(accounts, state.last_sender_id, campaign, now)
    if chosen is None:
        reason = "no_eligible_sender" + (f" ({len(rejected)} checked)" if rejected else " (none configured)")
        _record(db, brand_id, channel, RoutingMode.AUTO, "no_eligible", None, reason, message_id)
        return RoutingDecision(None, "no_eligible", RoutingMode.AUTO, reason)
    _consume(chosen, state, advance_cursor=True)
    _record(db, brand_id, channel, mode if mode != RoutingMode.FIXED else RoutingMode.AUTO, "assigned", chosen.id,
            "round_robin", message_id)
    if brand_contact is not None and getattr(brand_contact, STICKY_FIELD[channel]) is None:
        setattr(brand_contact, STICKY_FIELD[channel], chosen.id)
    return RoutingDecision(chosen, "assigned", mode, "round_robin")


def preview_next_sender(db: Session, brand_id: int, channel: str) -> tuple[SenderAccount | None, dict[int, str]]:
    """Read-only look-ahead for the dashboard (no lock, no counter change)."""
    state = db.scalars(select(SenderRoutingState).where(SenderRoutingState.brand_id == brand_id,
                                                        SenderRoutingState.channel == channel)).first()
    accounts = list(db.scalars(select(SenderAccount).where(SenderAccount.brand_id == brand_id,
                                                           SenderAccount.channel == channel,
                                                           SenderAccount.is_notification_sender.is_(False))
                               .order_by(SenderAccount.rotation_order, SenderAccount.id)))
    chosen, rejected = next_auto_sender(accounts, state.last_sender_id if state else None, None)
    for a in accounts:  # undo lazy counter roll-over done in memory
        db.expire(a)
    return chosen, rejected
