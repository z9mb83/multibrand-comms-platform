"""09:00 / 18:00 India-time activity reports — a port of report_core.py + daily_report.py.

Reads the Send Log, suppression list and lead statuses from the database, checks each outreach Gmail
conversation (metadata only) for replies, and emails one plain-text report. Never sends outreach and
never changes leads. A receipt row is claimed before sending, so a report is never delivered twice.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Brand, OutreachLead, OutreachReportReceipt, OutreachSendLog, SenderAccount
from app.models.outreach import OutreachSuppression
from app.providers.base import SendRequest
from app.providers.registry import get_provider
from app.services.outreach.engine import global_state, has_credentials

TZ = ZoneInfo("Asia/Kolkata")
HEADERS = ["From", "Subject", "Auto-Submitted", "X-Autoreply", "X-Autorespond"]


@dataclass
class Activity:
    brand: str
    sender: str
    recipient: str
    subject: str
    sent_at: datetime
    message_id: str
    result: str
    error: str = ""
    thread_id: str = ""


@dataclass
class Report:
    start: datetime
    end: datetime
    sent: list[Activity] = field(default_factory=list)
    failed: list[Activity] = field(default_factory=list)
    replies: list[dict] = field(default_factory=list)
    automated: list[dict] = field(default_factory=list)
    pending: list[Activity] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def window(slot: str, run_date: date, tz=TZ):
    midnight = datetime.combine(run_date, time(), tzinfo=tz)
    if slot == "morning":
        return midnight - timedelta(days=1), midnight
    if slot == "evening":
        return midnight, midnight.replace(hour=18)
    raise ValueError("Unknown report slot")


def replies_in_thread(messages, events, end):
    """Only inbound messages from a contacted lead after its outreach are replies.
    Automated responses are shown separately. Replies in new/unrelated threads are not inferred."""
    replies, automated, responded = [], [], set()
    own = {e.sender for e in events}
    for message in messages:
        if {"SENT", "DRAFT", "SPAM", "TRASH"}.intersection(message.get("labelIds", [])):
            continue
        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        sender = parseaddr(headers.get("from", ""))[1].lower()
        if sender in own:
            continue
        stamp = datetime.fromtimestamp(int(message["internalDate"]) / 1000, end.tzinfo)
        if stamp >= end:
            continue
        related = [e for e in events if e.recipient == sender and e.sent_at < stamp]
        auto = (headers.get("auto-submitted", "no").lower() != "no"
                or bool(headers.get("x-autoreply") or headers.get("x-autorespond"))
                or sender.split("@")[0] in {"mailer-daemon", "postmaster"})
        if not related and not auto:
            continue
        if not related:
            related = [e for e in events if e.sent_at < stamp]
        if not related:
            continue
        event = max(related, key=lambda e: e.sent_at)
        item = dict(brand=event.brand, mailbox=event.sender, sender=sender,
                    subject=headers.get("subject", ""), time=stamp, id=message["id"])
        if auto:
            automated.append(item)
        else:
            replies.append(item)
            responded.update((e.sender, e.message_id) for e in related)
    return replies, automated, responded


def render(report: Report, slot: str) -> tuple[str, str]:
    label = "9 AM previous-day report" if slot == "morning" else "6 PM daily report"
    subject = f"Email automation | {label} | {report.start:%d %b %Y}"
    if report.warnings:
        subject = "[PARTIAL] " + subject
    lines = [label, f"Period: {report.start:%d %b %Y %H:%M} to {report.end:%d %b %Y %H:%M} (India time; end excluded)",
             "", f"Sent: {len(report.sent)} | Failed attempts: {len(report.failed)} | Replies: {len(report.replies)}",
             f"Automated responses/bounces: {len(report.automated)} | Awaiting reply: {len(report.pending)}", ""]
    for title, items in (("EMAILS SENT", report.sent), ("FAILED SEND ATTEMPTS", report.failed)):
        lines.append(title)
        lines.extend(f"- {e.sent_at:%d %b %H:%M} | {e.brand} | {e.sender} -> {e.recipient} | {e.subject}"
                     + (f" | {e.error}" if title.startswith("FAILED") else "") for e in items)
        if not items:
            lines.append("None recorded.")
        lines.append("")
    for title, items in (("REPLIES RECEIVED", report.replies), ("AUTOMATED RESPONSES / BOUNCES", report.automated)):
        lines.append(title)
        lines.extend(f"- {r['time']:%d %b %H:%M} | {r['brand']} | {r['sender']} -> {r['mailbox']} | {r['subject']}" for r in items)
        if not items:
            lines.append("None found in successfully checked outreach conversations.")
        lines.append("")
    lines.append("AWAITING REPLY / FOLLOW-UP REVIEW")
    lines.extend(f"- {e.brand} | {e.recipient} | last sent {e.sent_at:%d %b %Y %H:%M} from {e.sender} | {e.subject}" for e in report.pending)  # noqa: E501
    if not report.pending:
        lines.append("None found in successfully checked conversations.")
    lines.extend(["", "Follow-up review covers previous outreach still awaiting a reply at the report cutoff, excluding suppressed/stopped leads.",  # noqa: E501
                  "No automatic follow-up emails are sent to leads by this report.",
                  "Replies are checked in Gmail conversations linked to successful Send Log entries, including older outreach.",
                  "Replies in unrelated/new threads or delivered only to a separate CC mailbox are outside this report's coverage.",
                  "Reply subjects and timestamps are included; message bodies are not read."])
    if report.warnings:
        lines.extend(["", "INCOMPLETE DATA — do not interpret missing data as zero activity:", *[f"- {w}" for w in report.warnings]])
    return subject, "\n".join(lines)


def error_kind(error) -> str:
    # Never echo a token-bearing credential/API response to report recipients.
    text = str(error).lower()
    if "invalid_grant" in text:
        return "OAuth invalid_grant; mailbox needs to be connected again"
    status = getattr(getattr(error, "response", None), "status_code", None)
    return f"API HTTP {status}" if status else type(error).__name__


class GmailReader:
    """Live Gmail metadata reads for one mailbox (profile check, message -> thread, thread metadata)."""

    def __init__(self, sender: SenderAccount):
        from app.providers.gmail import GmailProvider
        self.gp = GmailProvider
        self.token = GmailProvider.access_token(sender.secret_ref)
        self.sender = sender

    def profile_email(self) -> str:
        import httpx

        from app.providers.gmail import API
        r = httpx.get(f"{API}/profile", headers={"Authorization": f"Bearer {self.token}"}, timeout=20)
        r.raise_for_status()
        return str(r.json().get("emailAddress", "")).lower()

    def thread_id_for(self, message_id: str) -> str:
        import httpx

        from app.providers.gmail import API
        r = httpx.get(f"{API}/messages/{message_id}", params={"format": "minimal"},
                      headers={"Authorization": f"Bearer {self.token}"}, timeout=20)
        r.raise_for_status()
        return r.json()["threadId"]

    def thread(self, thread_id: str) -> dict:
        return self.gp.thread_metadata(None, thread_id, HEADERS, token=self.token)


def default_reader(sender: SenderAccount):
    if sender.provider != "gmail":
        return None  # simulated mailboxes have no inbox to check
    from app.core.config import get_settings
    if get_settings().provider_mode == "mock":
        return None
    return GmailReader(sender)


def collect(db: Session, start: datetime, end: datetime, reader_factory=default_reader) -> Report:
    report = Report(start, end)
    brands = db.scalars(select(Brand).where(Brand.status == "active").order_by(Brand.name)).all()
    blocked: set[tuple[str, str]] = set()
    events: list[Activity] = []
    for b in brands:
        rows = db.scalars(select(OutreachSendLog).where(OutreachSendLog.brand_id == b.id, OutreachSendLog.logged_at < end)
                          .order_by(OutreachSendLog.logged_at)).all()
        for row in rows:
            a = Activity(b.name, row.sender_email.lower(), row.lead_email.lower(), row.subject, row.logged_at.astimezone(start.tzinfo),
                         row.gmail_message_id, row.result, row.error, row.gmail_thread_id)
            if a.result == "Sent" and start <= a.sent_at:
                report.sent.append(a)
            elif a.result == "Failed" and start <= a.sent_at:
                report.failed.append(a)
            elif a.result == "Sending" and start <= a.sent_at:
                report.warnings.append(f"{b.name}: send to {a.recipient} at {a.sent_at:%d %b %H:%M} has uncertain delivery; "
                                       "check the mailbox's Sent folder.")
            if a.result == "Sent":
                events.append(a)
        for (email,) in db.execute(select(OutreachSuppression.email).where(OutreachSuppression.brand_id == b.id)):
            blocked.add((b.name, email.lower()))
        for email, status, follow in db.execute(select(OutreachLead.email, OutreachLead.status, OutreachLead.follow_up_status)
                                                .where(OutreachLead.brand_id == b.id)):
            if status in {"Unsubscribed", "Bounced", "Replied"} or follow in {"Stopped", "Completed", "Replied"}:
                blocked.add((b.name, (email or "").strip().lower()))

    senders = {s.address.lower(): s for s in db.scalars(select(SenderAccount).where(SenderAccount.channel == "email"))}
    readers: dict[str, object] = {}
    unavailable: set[str] = set()
    threads: dict[tuple[str, str], list[Activity]] = defaultdict(list)
    simulated = 0
    for e in events:
        if not e.message_id:
            report.warnings.append(f"{e.brand}: a sent row has no Gmail message ID; reply status unknown.")
            continue
        if e.sender in unavailable:
            continue
        if e.sender not in readers:
            sender = senders.get(e.sender)
            try:
                if sender is None or not has_credentials(sender):
                    raise LookupError("mailbox is not connected")
                reader = reader_factory(sender)
                if reader is not None and reader.profile_email() != e.sender:
                    raise ValueError("OAuth profile does not match configured sender")
                readers[e.sender] = reader
            except Exception as error:  # noqa: BLE001
                unavailable.add(e.sender)
                report.warnings.append(f"{e.sender}: reply check unavailable ({error_kind(error) if not isinstance(error, LookupError) else error}).")  # noqa: E501
                continue
        reader = readers[e.sender]
        if reader is None:
            simulated += 1
            continue
        try:
            tid = e.thread_id or reader.thread_id_for(e.message_id)
            threads[(e.sender, tid)].append(e)
        except Exception as error:  # noqa: BLE001
            report.warnings.append(f"{e.sender}: original message {e.message_id} unavailable ({error_kind(error)}).")

    seen_reply, seen_auto, pending, replied_leads = set(), set(), {}, set()
    for (mailbox, thread_id), sent_events in threads.items():
        try:
            thread = readers[mailbox].thread(thread_id)
            replies, automated, responded = replies_in_thread(thread.get("messages", []), sent_events, end)
            replied_leads.update((x.brand, x.recipient) for x in sent_events if (x.sender, x.message_id) in responded)
            for target, found, seen in ((report.replies, replies, seen_reply), (report.automated, automated, seen_auto)):
                for reply in found:
                    key = (mailbox, reply["id"])
                    if start <= reply["time"] < end and key not in seen:
                        target.append(reply)
                        seen.add(key)
            for x in sent_events:
                key = (x.brand, x.recipient)
                if key not in blocked and (x.sender, x.message_id) not in responded:
                    if key not in pending or pending[key].sent_at < x.sent_at:
                        pending[key] = x
        except Exception as error:  # noqa: BLE001
            report.warnings.append(f"{mailbox}: conversation {thread_id} reply status unknown ({error_kind(error)}).")
    report.pending = sorted((x for key, x in pending.items() if key not in replied_leads), key=lambda x: x.sent_at)
    if simulated:
        report.warnings.append(f"{simulated} sent email(s) came from simulated mailboxes, so replies were not checked.")
    report.sent.sort(key=lambda x: x.sent_at)
    report.failed.sort(key=lambda x: x.sent_at)
    report.replies.sort(key=lambda x: x["time"])
    report.automated.sort(key=lambda x: x["time"])
    report.warnings = list(dict.fromkeys(report.warnings))
    return report


def build(db: Session, slot: str, run_date: date, reader_factory=default_reader) -> tuple[str, str, Report]:
    start, end = window(slot, run_date)
    report = collect(db, start, end, reader_factory)
    subject, body = render(report, slot)
    return subject, body, report


def run_scheduled(db: Session, slot: str, *, now: datetime | None = None, reader_factory=default_reader) -> dict:
    """Called by the scheduler at 09:00 / 18:00 India time. Idempotent per recipient + slot + date."""
    g = global_state(db)
    db.commit()
    if not g.reports_enabled:
        return {"status": "disabled"}
    now = (now or datetime.now(timezone.utc)).astimezone(TZ)
    if now.hour < (9 if slot == "morning" else 18):
        return {"status": "too_early"}
    recipient, sender_addr = g.report_recipient.strip().lower(), g.report_sender.strip().lower()
    sender = db.scalars(select(SenderAccount).where(SenderAccount.channel == "email",
                                                    SenderAccount.address == sender_addr)).first()
    if sender is None or not has_credentials(sender):
        return {"status": "error", "detail": f"Report sender {sender_addr} is not a connected mailbox"}
    receipt = OutreachReportReceipt(recipient=recipient, slot=slot, report_date=now.date(), status="sending")
    db.add(receipt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"status": "already_sent"}
    subject, body, _ = build(db, slot, now.date(), reader_factory)
    receipt.subject, receipt.body = subject, body
    db.commit()
    res = get_provider("email", sender.provider).send(SendRequest(
        channel="email", from_address=sender.address, to_address=recipient, subject=subject, body=body, plain_text=True,
        idempotency_key=f"outreach-report-{receipt.id}", secret_ref=sender.secret_ref, sender_config=sender.provider_config or {}))
    if not res.ok:
        # Left in "sending": delivery is uncertain, so it is never retried automatically.
        receipt.body = f"{body}\n\n[delivery error] {res.error}"[:100000]
        db.commit()
        return {"status": "error", "detail": res.error}
    receipt.status, receipt.message_id, receipt.completed_at = "sent", res.provider_message_id or "", datetime.now(timezone.utc)
    db.commit()
    return {"status": "sent", "subject": subject}
