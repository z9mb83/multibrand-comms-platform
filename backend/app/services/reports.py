"""Daily morning/evening master reports and their delivery with safe retries.

Figures come only from recorded facts: 'sent' = provider accepted (status sent/delivered/read),
'completed' = task completed by the system or confirmed by a user.
"""
import html
import io
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import (
    Brand,
    Campaign,
    Conversation,
    DailyTask,
    FollowUpTask,
    InboundMessage,
    MasterReport,
    MasterReportSettings,
    NotificationDelivery,
    OutboundMessage,
    SenderAccount,
    User,
)
from app.models.enums import CampaignStatus, ConversationStatus, DeliveryStatus, TaskStatus
from app.providers.base import SendRequest
from app.providers.registry import get_provider
from app.services.common import DomainError, audit, now_utc

SENT = ("sent", "delivered", "read")


def get_settings_row(db: Session) -> MasterReportSettings:
    s = db.get(MasterReportSettings, 1)
    if s is None:
        s = MasterReportSettings(id=1, working_days=[0, 1, 2, 3, 4, 5], brand_preferences={}, report_recipients=[])
        db.add(s)
        db.flush()
    return s


def _day_bounds(d: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _included_brands(db: Session, s: MasterReportSettings) -> list[Brand]:
    brands = db.scalars(select(Brand).where(Brand.status == "active").order_by(Brand.name)).all()
    prefs = s.brand_preferences or {}
    return [b for b in brands if prefs.get(str(b.id), {}).get("include", True)]


def _brand_section(db: Session, brand: Brand, kind: str, d: date, tz: ZoneInfo, now: datetime) -> dict:
    today_s, today_e = _day_bounds(d, tz)
    prev_s, prev_e = _day_bounds(d - timedelta(days=1), tz)
    tom_s, tom_e = _day_bounds(d + timedelta(days=1), tz)
    b = brand.id
    # activity window: morning report looks at yesterday, evening report at today
    win_s, win_e = (prev_s, prev_e) if kind == "morning" else (today_s, today_e)

    def count(q):
        return db.scalar(q) or 0

    sent_by_channel = dict(db.execute(
        select(OutboundMessage.channel, func.count()).where(OutboundMessage.brand_id == b,
                                                            OutboundMessage.status.in_(SENT),
                                                            OutboundMessage.sent_at >= win_s,
                                                            OutboundMessage.sent_at < win_e)
        .group_by(OutboundMessage.channel)).all())
    status_counts = dict(db.execute(
        select(OutboundMessage.status, func.count()).where(OutboundMessage.brand_id == b,
                                                           OutboundMessage.updated_at >= win_s,
                                                           OutboundMessage.updated_at < win_e)
        .group_by(OutboundMessage.status)).all())
    new_replies = count(select(func.count()).select_from(InboundMessage).where(
        InboundMessage.brand_id == b, InboundMessage.received_at >= win_s, InboundMessage.received_at < win_e))
    unanswered = count(select(func.count()).select_from(Conversation).where(
        Conversation.brand_id == b, Conversation.status == ConversationStatus.NEEDS_REPLY))
    completed = db.scalars(select(DailyTask).where(DailyTask.brand_id == b, DailyTask.status == TaskStatus.COMPLETED,
                                                   DailyTask.completed_at >= win_s, DailyTask.completed_at < win_e)).all()
    fu_due_today = count(select(func.count()).select_from(FollowUpTask).where(
        FollowUpTask.brand_id == b, FollowUpTask.status == "pending", FollowUpTask.due_at >= today_s,
        FollowUpTask.due_at < today_e))
    fu_overdue = count(select(func.count()).select_from(FollowUpTask).where(
        FollowUpTask.brand_id == b, FollowUpTask.status.in_(["pending", "awaiting_approval"]), FollowUpTask.due_at < now))
    fu_completed = count(select(func.count()).select_from(FollowUpTask).where(
        FollowUpTask.brand_id == b, FollowUpTask.status.in_(["completed", "approved"]),
        FollowUpTask.updated_at >= win_s, FollowUpTask.updated_at < win_e))
    campaigns_today = db.scalars(select(Campaign.name).where(
        Campaign.brand_id == b, Campaign.scheduled_at >= today_s, Campaign.scheduled_at < today_e,
        Campaign.status.in_([CampaignStatus.APPROVED, CampaignStatus.RUNNING]))).all()
    failed = count(select(func.count()).select_from(OutboundMessage).where(
        OutboundMessage.brand_id == b, OutboundMessage.status.in_(["failed", "held_for_review", "unknown"])))
    sched_window = (today_s, today_e) if kind == "morning" else (tom_s, tom_e)
    task_rows = db.execute(select(DailyTask, User.full_name).outerjoin(User, User.id == DailyTask.assigned_user_id)
                           .where(DailyTask.brand_id == b, DailyTask.due_at >= sched_window[0],
                                  DailyTask.due_at < sched_window[1],
                                  DailyTask.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]))
                           .order_by(DailyTask.due_at)).all()
    unfinished = count(select(func.count()).select_from(DailyTask).where(
        DailyTask.brand_id == b, DailyTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE,
                                                       TaskStatus.BLOCKED]), DailyTask.due_at < today_e))
    return {
        "brand_id": b, "brand": brand.name,
        "messages_sent": sum(sent_by_channel.values()),
        "sent_by_channel": {ch: sent_by_channel.get(ch, 0) for ch in ("email", "whatsapp", "sms")},
        "status_counts": status_counts,
        "new_replies": new_replies, "unanswered_conversations": unanswered,
        "completed_tasks": [{"id": t.id, "description": t.description} for t in completed],
        "follow_ups_due_today": fu_due_today, "overdue_follow_ups": fu_overdue, "follow_ups_completed": fu_completed,
        "campaigns_scheduled_today": list(campaigns_today), "failed_sends_attention": failed,
        "scheduled_tasks": [{"id": t.id, "description": t.description, "owner": name or "Unassigned",
                             "due": t.due_at.astimezone(tz).strftime("%H:%M"), "priority": t.priority}
                            for t, name in task_rows],
        "unfinished_tasks": unfinished,
    }


def build(db: Session, kind: str, report_date: date, now: datetime | None = None) -> dict:
    if kind not in ("morning", "evening"):
        raise DomainError("kind must be morning or evening")
    s = get_settings_row(db)
    tz = ZoneInfo(s.timezone)
    now = now or now_utc()
    sections = [_brand_section(db, b, kind, report_date, tz, now) for b in _included_brands(db, s)]
    keys = ["messages_sent", "new_replies", "unanswered_conversations", "follow_ups_due_today", "overdue_follow_ups",
            "follow_ups_completed", "failed_sends_attention", "unfinished_tasks"]
    overall = {k: sum(sec[k] for sec in sections) for k in keys}
    overall["sent_by_channel"] = {ch: sum(sec["sent_by_channel"][ch] for sec in sections) for ch in ("email", "whatsapp", "sms")}
    overall["completed_tasks"] = sum(len(sec["completed_tasks"]) for sec in sections)
    overall["scheduled_tasks"] = sum(len(sec["scheduled_tasks"]) for sec in sections)
    actions = []
    if overall["failed_sends_attention"]:
        actions.append(f"{overall['failed_sends_attention']} failed/held sends need review")
    if overall["overdue_follow_ups"]:
        actions.append(f"{overall['overdue_follow_ups']} follow-ups are overdue")
    if overall["unanswered_conversations"]:
        actions.append(f"{overall['unanswered_conversations']} conversations await a reply")
    return {"kind": kind, "date": report_date.isoformat(), "timezone": s.timezone, "overall": overall,
            "brands": sections, "action_items": actions}


def render_html(data: dict) -> str:
    e = html.escape
    o = data["overall"]
    title = f"{'Morning' if data['kind'] == 'morning' else 'Evening'} report — {data['date']}"
    rows = "".join(
        f"<tr><td>{e(b['brand'])}</td><td>{b['sent_by_channel']['email']}</td><td>{b['sent_by_channel']['whatsapp']}</td>"
        f"<td>{b['sent_by_channel']['sms']}</td><td>{b['new_replies']}</td><td>{b['unanswered_conversations']}</td>"
        f"<td>{b['follow_ups_due_today']}</td><td>{b['overdue_follow_ups']}</td><td>{b['failed_sends_attention']}</td>"
        f"<td>{len(b['completed_tasks'])}</td><td>{b['unfinished_tasks']}</td></tr>" for b in data["brands"])
    tasks = "".join(
        f"<tr><td>{e(b['brand'])}</td><td>{t['due']}</td><td>{e(t['description'])}</td><td>{e(t['owner'])}</td>"
        f"<td>{e(t['priority'])}</td></tr>" for b in data["brands"] for t in b["scheduled_tasks"])
    actions = "".join(f"<li>{e(a)}</li>" for a in data["action_items"]) or "<li>No action items.</li>"
    sched_label = "Today's schedule" if data["kind"] == "morning" else "Tomorrow's scheduled work"
    th = "style='text-align:left;padding:6px;border-bottom:1px solid #ddd;background:#f4f4f5'"
    return f"""<!doctype html><html><body style="font-family:Arial,sans-serif;color:#111">
<h2>{e(title)}</h2>
<p><b>{o['messages_sent']}</b> messages sent (email {o['sent_by_channel']['email']}, WhatsApp {o['sent_by_channel']['whatsapp']},
SMS {o['sent_by_channel']['sms']}) · <b>{o['new_replies']}</b> new replies · <b>{o['completed_tasks']}</b> tasks completed</p>
<h3>Action items</h3><ul>{actions}</ul>
<h3>By brand</h3>
<table cellspacing="0" style="border-collapse:collapse;font-size:13px"><tr>
<th {th}>Brand</th><th {th}>Email</th><th {th}>WhatsApp</th><th {th}>SMS</th><th {th}>Replies</th><th {th}>Unanswered</th>
<th {th}>FU due</th><th {th}>FU overdue</th><th {th}>Failed/held</th><th {th}>Done</th><th {th}>Open tasks</th></tr>{rows}</table>
<h3>{sched_label}</h3>
<table cellspacing="0" style="border-collapse:collapse;font-size:13px"><tr><th {th}>Brand</th><th {th}>Time</th>
<th {th}>Task</th><th {th}>Owner</th><th {th}>Priority</th></tr>{tasks or '<tr><td colspan=5>Nothing scheduled.</td></tr>'}</table>
<p style="color:#666;font-size:12px">Timezone: {e(data['timezone'])}.
Figures reflect provider-accepted sends and recorded task completions only.</p>
</body></html>"""


def render_mobile(data: dict) -> str:
    o = data["overall"]
    label = "Morning" if data["kind"] == "morning" else "Evening"
    lines = [f"{label} report {data['date']}",
             f"Sent {o['messages_sent']} (E{o['sent_by_channel']['email']}/"
             f"W{o['sent_by_channel']['whatsapp']}/S{o['sent_by_channel']['sms']})",
             f"Replies {o['new_replies']} | Unanswered {o['unanswered_conversations']}",
             f"FU due {o['follow_ups_due_today']} | Overdue {o['overdue_follow_ups']} | Failed {o['failed_sends_attention']}"]
    lines += [f"{b['brand']}: {b['messages_sent']} sent, {b['new_replies']} replies" for b in data["brands"]]
    return "\n".join(lines)[:1000]


def spreadsheet(data: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "By brand"
    ws.append(["Brand", "Email", "WhatsApp", "SMS", "Replies", "Unanswered", "FU due", "FU overdue", "Failed/held",
               "Completed tasks", "Open tasks"])
    for b in data["brands"]:
        ws.append([b["brand"], b["sent_by_channel"]["email"], b["sent_by_channel"]["whatsapp"], b["sent_by_channel"]["sms"],
                   b["new_replies"], b["unanswered_conversations"], b["follow_ups_due_today"], b["overdue_follow_ups"],
                   b["failed_sends_attention"], len(b["completed_tasks"]), b["unfinished_tasks"]])
    ws2 = wb.create_sheet("Tasks")
    ws2.append(["Brand", "Due", "Task", "Owner", "Priority"])
    for b in data["brands"]:
        for t in b["scheduled_tasks"]:
            ws2.append([b["brand"], t["due"], t["description"], t["owner"], t["priority"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate(db: Session, kind: str, report_date: date, now: datetime | None = None, force: bool = False) -> MasterReport:
    existing = db.scalars(select(MasterReport).where(MasterReport.kind == kind, MasterReport.report_date == report_date)).first()
    if existing and not force:
        return existing
    data = build(db, kind, report_date, now)
    if existing:
        existing.data, existing.html, existing.mobile_text = data, render_html(data), render_mobile(data)
        existing.generated_at = now_utc()
        report = existing
    else:
        report = MasterReport(kind=kind, report_date=report_date, data=data, html=render_html(data),
                              mobile_text=render_mobile(data), status="generated")
        db.add(report)
        db.flush()
    _create_deliveries(db, report)
    return report


def _create_deliveries(db: Session, report: MasterReport) -> None:
    s = get_settings_row(db)
    targets: list[tuple[str, str]] = []
    ch = s.notification_channel
    if "email" in ch:
        for r in [s.master_email, *(s.report_recipients or [])]:
            if r:
                targets.append(("email", r))
    if "whatsapp" in ch and s.master_mobile:
        targets.append(("whatsapp", s.master_mobile))
    if "sms" in ch and s.master_mobile:
        targets.append(("sms", s.master_mobile))
    for channel, recipient in targets:
        key = f"report-{report.id}-{channel}-{recipient}"
        if db.scalar(select(NotificationDelivery.id).where(NotificationDelivery.idempotency_key == key)):
            continue
        db.add(NotificationDelivery(report_id=report.id, kind="report", channel=channel, recipient=recipient,
                                    status=DeliveryStatus.PENDING, idempotency_key=key, next_attempt_at=now_utc()))
    db.flush()


def _notification_sender(db: Session, s: MasterReportSettings, channel: str) -> SenderAccount | None:
    sid = {"email": s.notification_email_sender_id, "whatsapp": s.notification_whatsapp_sender_id,
           "sms": s.notification_sms_sender_id}[channel]
    return db.get(SenderAccount, sid) if sid else None


def deliver_pending(db: Session, now: datetime | None = None) -> dict:
    """Send due notification deliveries. Uses dedicated notification senders, never marketing rotation."""
    now = now or now_utc()
    s = get_settings_row(db)
    stats = {"sent": 0, "retrying": 0, "failed": 0}
    rows = db.scalars(select(NotificationDelivery).where(
        NotificationDelivery.status.in_([DeliveryStatus.PENDING, DeliveryStatus.RETRYING]),
        (NotificationDelivery.next_attempt_at.is_(None)) | (NotificationDelivery.next_attempt_at <= now))
        .with_for_update(skip_locked=True)).all()
    for d in rows:
        report = db.get(MasterReport, d.report_id) if d.report_id else None
        sender = _notification_sender(db, s, d.channel)
        d.attempts += 1
        if sender is None:
            result_ok, err, retryable, pid = False, f"no notification sender configured for {d.channel}", False, None
        elif d.channel != "email" and s.mobile_opt_in_at is None:
            result_ok, err, retryable, pid = False, "master mobile has not opted in", False, None
        else:
            title = f"{report.kind.title()} report {report.report_date}" if report else d.payload.get("subject", "Alert")
            req = SendRequest(channel=d.channel, from_address=sender.address, from_name=sender.display_name,
                              to_address=d.recipient, subject=title,
                              body=(report.html if d.channel == "email" else report.mobile_text) if report else d.payload.get("text"),
                              template_name=s.whatsapp_report_template if d.channel == "whatsapp" else None,
                              template_params={"body": [report.mobile_text]} if (report and d.channel == "whatsapp") else {},
                              idempotency_key=d.idempotency_key, sender_config=sender.provider_config or {},
                              secret_ref=sender.secret_ref)
            if report and d.channel == "email" and s.attach_spreadsheet:
                req.attachments.append((f"{report.kind}-report-{report.report_date}.xlsx", spreadsheet(report.data),
                                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
            res = get_provider(d.channel, sender.provider).send(req)
            result_ok, err, retryable, pid = res.ok, res.error, res.retryable, res.provider_message_id
        if result_ok:
            d.status, d.sent_at, d.provider_message_id, d.last_error = DeliveryStatus.SENT, now, pid, None
            stats["sent"] += 1
        elif retryable and d.attempts < d.max_attempts:
            d.status, d.last_error = DeliveryStatus.RETRYING, err
            d.next_attempt_at = now + timedelta(minutes=2 ** d.attempts)
            stats["retrying"] += 1
        else:
            d.status, d.last_error = DeliveryStatus.FAILED, err
            stats["failed"] += 1
        if report is not None:
            db.flush()
            _refresh_report_status(db, report)
    return stats


def _refresh_report_status(db: Session, report: MasterReport) -> None:
    statuses = db.scalars(select(NotificationDelivery.status).where(NotificationDelivery.report_id == report.id)).all()
    if statuses and all(x == "sent" for x in statuses):
        report.status = "delivered"
    elif any(x == "sent" for x in statuses):
        report.status = "partially_delivered"
    elif statuses and all(x == "failed" for x in statuses):
        report.status = "failed"


def run_due(db: Session, now: datetime | None = None) -> list[MasterReport]:
    """Called by Cloud Scheduler every ~15 min. Idempotent via unique (kind, report_date)."""
    now = now or now_utc()
    s = get_settings_row(db)
    tz = ZoneInfo(s.timezone)
    local = now.astimezone(tz)
    if local.weekday() not in (s.working_days or []):
        return []
    made = []
    for kind, hhmm in (("morning", s.morning_time), ("evening", s.evening_time)):
        h, m = (int(x) for x in hhmm.split(":"))
        due_at = local.replace(hour=h, minute=m, second=0, microsecond=0)
        # generate within a 3-hour window after the configured time (covers scheduler hiccups)
        if due_at <= local < due_at + timedelta(hours=3):
            exists = db.scalar(select(MasterReport.id).where(and_(MasterReport.kind == kind,
                                                                  MasterReport.report_date == local.date())))
            if not exists:
                made.append(generate(db, kind, local.date(), now))
                audit(db, "report.generated", entity_type="master_report", entity_id=made[-1].id, kind=kind)
    return made
