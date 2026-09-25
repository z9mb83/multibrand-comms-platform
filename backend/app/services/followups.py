"""Follow-up workflow engine.

Rule steps are relative to the initial send, e.g. Day 1 send, Day 3 first follow-up,
Day 7 second follow-up == steps [{"after_days": 2}, {"after_days": 6}].
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BrandContact, Conversation, DailyTask, FollowUpRule, FollowUpTask, InboundMessage, OutboundMessage
from app.models.enums import ConversationStatus, FollowUpMode, FollowUpStatus, MessageStatus, RoutingMode, TaskStatus
from app.services.common import DomainError, audit, now_utc
from app.services.contacts import check_can_send

OPEN_STATUSES = (FollowUpStatus.PENDING, FollowUpStatus.AWAITING_APPROVAL, FollowUpStatus.APPROVED)
TASK_CREATED = "task_created"


def rule_for(db: Session, brand_id: int, campaign_id: int | None) -> FollowUpRule | None:
    if campaign_id:
        r = db.scalars(select(FollowUpRule).where(FollowUpRule.brand_id == brand_id,
                                                  FollowUpRule.campaign_id == campaign_id,
                                                  FollowUpRule.active.is_(True))).first()
        if r:
            return r
    return None


def schedule_initial_followup(db: Session, msg: OutboundMessage) -> FollowUpTask | None:
    """Called after a campaign message is sent (not for follow-up messages themselves)."""
    if msg.follow_up_task_id or not msg.campaign_id or not msg.conversation_id:
        return None
    rule = rule_for(db, msg.brand_id, msg.campaign_id)
    if rule is None or not rule.steps:
        return None
    return _create_step(db, rule, msg.conversation_id, msg.contact_id, msg.campaign_id, 0, msg.sent_at or now_utc())


def _create_step(db: Session, rule: FollowUpRule, conversation_id: int, contact_id: int, campaign_id: int | None,
                 index: int, base_time) -> FollowUpTask | None:
    if index >= len(rule.steps) or index >= rule.max_attempts:
        return None
    exists = db.scalars(select(FollowUpTask).where(FollowUpTask.conversation_id == conversation_id,
                                                   FollowUpTask.rule_id == rule.id,
                                                   FollowUpTask.step_index == index)).first()
    if exists:
        return exists
    step = rule.steps[index]
    mode = step.get("mode", FollowUpMode.SCHEDULED)
    conv = db.get(Conversation, conversation_id)
    t = FollowUpTask(brand_id=rule.brand_id, rule_id=rule.id, conversation_id=conversation_id, contact_id=contact_id,
                     campaign_id=campaign_id, step_index=index, mode=mode, template_id=step.get("template_id"),
                     due_at=base_time + timedelta(days=step.get("after_days", 2)), status=FollowUpStatus.PENDING,
                     assigned_user_id=step.get("assignee_id") or rule.default_assignee_id or (conv.owner_user_id if conv else None))
    db.add(t)
    db.flush()
    return t


def stop_reason(db: Session, task: FollowUpTask, rule: FollowUpRule | None) -> str | None:
    conv = db.get(Conversation, task.conversation_id) if task.conversation_id else None
    if conv is not None:
        if conv.status == ConversationStatus.CLOSED:
            return "conversation_closed"
        if conv.followups_paused:
            return "paused_by_human_or_reply"
        if rule is None or rule.stop_on_reply:
            replied = db.scalar(select(InboundMessage.id).where(InboundMessage.conversation_id == conv.id,
                                                                InboundMessage.handled.is_(False)).limit(1))
            if replied:
                return "contact_replied"
    bc = db.scalars(select(BrandContact).where(BrandContact.brand_id == task.brand_id,
                                               BrandContact.contact_id == task.contact_id)).first()
    if conv is not None and bc is not None:
        addr = bc.email if conv.channel == "email" else bc.phone
        if addr:
            reason = check_can_send(db, task.brand_id, task.contact_id, conv.channel, addr)
            if reason:
                return reason
    if rule is not None and task.step_index >= rule.max_attempts:
        return "max_attempts_reached"
    return None


def run_sweep(db: Session, now=None) -> dict:
    """Process due follow-up tasks. Idempotent: each task transitions out of PENDING once."""
    now = now or now_utc()
    stats = {"tasks_created": 0, "awaiting_approval": 0, "cancelled": 0}
    due = db.scalars(select(FollowUpTask).where(FollowUpTask.status == FollowUpStatus.PENDING,
                                                FollowUpTask.due_at <= now).with_for_update(skip_locked=True)).all()
    for t in due:
        rule = db.get(FollowUpRule, t.rule_id) if t.rule_id else None
        reason = stop_reason(db, t, rule)
        if reason:
            if reason == "contact_replied":
                t.status = FollowUpStatus.PAUSED_ON_REPLY
            else:
                t.status, t.cancel_reason = FollowUpStatus.CANCELLED, reason
            stats["cancelled"] += 1
            continue
        bc = db.scalars(select(BrandContact).where(BrandContact.brand_id == t.brand_id,
                                                   BrandContact.contact_id == t.contact_id)).first()
        who = (bc.contact_name or bc.company_name or bc.email or bc.phone) if bc else f"contact {t.contact_id}"
        if t.mode == FollowUpMode.APPROVAL_SEND:
            t.status = FollowUpStatus.AWAITING_APPROVAL
            desc = f"Approve follow-up #{t.step_index + 1} to {who}"
            stats["awaiting_approval"] += 1
        else:
            t.status = TASK_CREATED
            desc = ("Reminder: " if t.mode == FollowUpMode.REMINDER else "Follow up with ") + f"{who} (step {t.step_index + 1})"
            stats["tasks_created"] += 1
        db.add(DailyTask(brand_id=t.brand_id, contact_id=t.contact_id,
                         company_name=bc.company_name if bc else None, assigned_user_id=t.assigned_user_id,
                         description=desc, priority="high" if t.step_index > 0 else "medium", due_at=now,
                         status=TaskStatus.PENDING, conversation_id=t.conversation_id, follow_up_task_id=t.id,
                         source="follow_up"))
        # schedule next step relative to the original send
        if rule is not None and t.conversation_id:
            first = db.scalars(select(FollowUpTask).where(FollowUpTask.conversation_id == t.conversation_id,
                                                          FollowUpTask.rule_id == rule.id,
                                                          FollowUpTask.step_index == 0)).first()
            base = (first.due_at - timedelta(days=rule.steps[0].get("after_days", 2))) if first else now
            _create_step(db, rule, t.conversation_id, t.contact_id, t.campaign_id, t.step_index + 1, base)
    return stats


def approve_and_send(db: Session, task_id: int, user_id: int, body: str | None = None,
                     subject: str | None = None) -> OutboundMessage:
    from app.services.dispatch import create_message  # avoid cycle

    t = db.get(FollowUpTask, task_id)
    if t is None:
        raise DomainError("follow-up not found", 404)
    if t.status != FollowUpStatus.AWAITING_APPROVAL:
        raise DomainError(f"follow-up is {t.status}, not awaiting approval", 409)
    rule = db.get(FollowUpRule, t.rule_id) if t.rule_id else None
    reason = stop_reason(db, t, rule)
    if reason:
        t.status, t.cancel_reason = FollowUpStatus.CANCELLED, reason
        raise DomainError(f"follow-up no longer eligible: {reason}", 409)
    conv = db.get(Conversation, t.conversation_id)
    bc = db.scalars(select(BrandContact).where(BrandContact.brand_id == t.brand_id,
                                               BrandContact.contact_id == t.contact_id)).one()
    msg = create_message(db, brand_id=t.brand_id, contact_id=t.contact_id, channel=conv.channel,
                         to_address=bc.email if conv.channel == "email" else bc.phone, subject=subject, body=body,
                         template_id=t.template_id, campaign_id=t.campaign_id, routing_mode=RoutingMode.STICKY,
                         status=MessageStatus.QUEUED, idempotency_key=f"fu-{t.id}", conversation_id=conv.id,
                         follow_up_task_id=t.id)
    t.status = FollowUpStatus.APPROVED
    audit(db, "follow_up.approved", user_id=user_id, brand_id=t.brand_id, entity_type="follow_up_task", entity_id=t.id)
    return msg


def pause_for_conversation(db: Session, conversation_id: int, reason: str = "reply") -> int:
    tasks = db.scalars(select(FollowUpTask).where(FollowUpTask.conversation_id == conversation_id,
                                                  FollowUpTask.status.in_(OPEN_STATUSES))).all()
    for t in tasks:
        t.status = FollowUpStatus.PAUSED_ON_REPLY if reason == "reply" else FollowUpStatus.PAUSED
    # queued follow-up messages that have not been sent yet are held too
    msgs = db.scalars(select(OutboundMessage).where(OutboundMessage.conversation_id == conversation_id,
                                                    OutboundMessage.follow_up_task_id.is_not(None),
                                                    OutboundMessage.status == MessageStatus.QUEUED)).all()
    for m in msgs:
        m.status, m.hold_reason = MessageStatus.HELD_FOR_REVIEW, f"follow_up_paused:{reason}"
    return len(tasks) + len(msgs)


def resume(db: Session, conversation_id: int, user_id: int) -> int:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise DomainError("conversation not found", 404)
    conv.followups_paused = False
    for im in db.scalars(select(InboundMessage).where(InboundMessage.conversation_id == conversation_id,
                                                      InboundMessage.handled.is_(False))):
        im.handled = True  # a human reviewed the reply before resuming
    tasks = db.scalars(select(FollowUpTask).where(FollowUpTask.conversation_id == conversation_id,
                                                  FollowUpTask.status.in_([FollowUpStatus.PAUSED_ON_REPLY,
                                                                           FollowUpStatus.PAUSED]))).all()
    for t in tasks:
        t.status = FollowUpStatus.PENDING
    audit(db, "follow_up.resumed", user_id=user_id, brand_id=conv.brand_id, entity_type="conversation",
          entity_id=conversation_id, count=len(tasks))
    return len(tasks)


def cancel_for_contact(db: Session, brand_id: int, contact_id: int, reason: str) -> int:
    tasks = db.scalars(select(FollowUpTask).where(FollowUpTask.brand_id == brand_id,
                                                  FollowUpTask.contact_id == contact_id,
                                                  FollowUpTask.status.in_([*OPEN_STATUSES, FollowUpStatus.PAUSED_ON_REPLY,
                                                                           FollowUpStatus.PAUSED]))).all()
    for t in tasks:
        t.status, t.cancel_reason = FollowUpStatus.CANCELLED, reason
    msgs = db.scalars(select(OutboundMessage).where(OutboundMessage.brand_id == brand_id,
                                                    OutboundMessage.contact_id == contact_id,
                                                    OutboundMessage.status.in_([MessageStatus.QUEUED, MessageStatus.DRAFT,
                                                                                MessageStatus.HELD_FOR_REVIEW]))).all()
    for m in msgs:
        m.status, m.hold_reason = MessageStatus.CANCELLED, reason
    return len(tasks)
