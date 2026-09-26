"""Email outreach engine — a port of the Sheet/Cloud Run automation (main.py, brand_runner.py,
limit_manager.py, approval_manager.py, duplicate_manager.py, suppression_manager.py).

Per run: brands in rotating order; per brand at most ONE email:
  emergency stop -> AI drafts for new leads -> first approved lead -> brand gap ->
  checks (email valid, subject/body, suppression, already contacted, cross-brand, sending hours) ->
  least-used eligible mailbox -> Gmail send (with CC) -> lead + Send Log + mailbox row updated.

One deliberate addition: a "Sending" Send Log row is committed before Gmail is called, so if the
database write after a successful send fails, the lead can never be emailed a second time.
"""
from __future__ import annotations

import re
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.db import get_engine
from app.models import Brand, OutreachGlobal, OutreachLead, OutreachMailbox, OutreachSendLog, OutreachSettings, SenderAccount
from app.models.outreach import OutreachSuppression
from app.providers.base import SendRequest
from app.providers.registry import get_provider
from app.services.outreach import generator

STATUS_VALUES = ["Pending", "Generated", "Approved", "Scheduled", "Sent", "Skipped", "Failed", "Replied",
                 "Unsubscribed", "Bounced"]
FOLLOW_UP_VALUES = ["Pending", "Replied", "Stopped", "Completed"]
SENDABLE_STATUSES = {"Generated", "Approved", "Scheduled"}
TERMINAL_GENERATION_STATUSES = {"Sent", "Unsubscribed", "Bounced"}
COUNTED = ("Sent", "Sending")  # a claimed-but-unconfirmed send counts, so it is never repeated

EMAIL_RE = re.compile(r"^[^\s@,]+@[^\s@,]+\.[^\s@,]+$")
RUN_LOCK_KEY = 4242002
GENERATION_BUDGET_SECONDS = 35  # keep one run inside the 60 s serverless limit


def yes(value) -> bool:
    return str(value or "").strip().lower() == "yes"


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip().lower()))


def normalize_email_address(raw) -> tuple[str, str | None]:
    original = str(raw or "").strip()
    if not original:
        return "", None
    lowered = original.lower()
    if EMAIL_RE.match(lowered):
        return lowered, None
    candidate = lowered.replace(" ", "").strip(",").replace(",", "")
    if candidate != lowered and EMAIL_RE.match(candidate):
        return candidate, f"Normalized recipient from {original} to {candidate}"
    return lowered, None


def can_generate(lead: OutreachLead) -> bool:
    recipient = (lead.email or "").strip()
    return bool(recipient and (not (lead.subject or "").strip() or not (lead.body or "").strip())
                and (lead.status or "").strip() not in TERMINAL_GENERATION_STATUSES)


def is_approved(lead: OutreachLead) -> bool:
    return yes(lead.approval) and (lead.status or "").strip() in SENDABLE_STATUSES


def valid_send_time(now: datetime, start_text: str, end_text: str) -> bool:
    try:
        sh, sm = [int(p) for p in start_text.split(":", 1)]
        eh, em = [int(p) for p in end_text.split(":", 1)]
    except (TypeError, ValueError, AttributeError):
        sh, sm, eh, em = 10, 0, 18, 0
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start <= now < end


# --------------------------------------------------------------------------- mailboxes
@dataclass
class Mailbox:
    sender: SenderAccount
    row: OutreachMailbox
    email: str
    enabled: bool
    stage: str
    daily_limit: int
    hourly_limit: int
    sent_today: int
    sent_this_hour: int
    last_sent_at: datetime | None
    next_eligible_at: datetime | None
    paused: bool
    has_credentials: bool


def has_credentials(sender: SenderAccount) -> bool:
    return sender.connection_status == "connected" and sender.status == "active"


def outreach_row(db: Session, sender: SenderAccount) -> OutreachMailbox:
    row = db.get(OutreachMailbox, sender.id)
    if row is None:
        row = OutreachMailbox(sender_account_id=sender.id, brand_id=sender.brand_id, enabled=True, stage="Stage 1",
                              clean_days=0, paused=False, pause_reason="", last_result="")
        db.add(row)
        db.flush()
    return row


def load_mailboxes(db: Session, brand_id: int, settings: OutreachSettings, now: datetime) -> list[Mailbox]:
    tz = now.tzinfo
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    senders = db.scalars(select(SenderAccount).where(SenderAccount.brand_id == brand_id, SenderAccount.channel == "email")
                         .order_by(SenderAccount.is_master.desc(), SenderAccount.rotation_order, SenderAccount.id)).all()
    counts: dict[str, list[int]] = {}
    for sender_email, logged_at in db.execute(
            select(OutreachSendLog.sender_email, OutreachSendLog.logged_at).where(
                OutreachSendLog.brand_id == brand_id, OutreachSendLog.result.in_(COUNTED),
                OutreachSendLog.logged_at >= day_start)):
        c = counts.setdefault(sender_email.lower(), [0, 0])
        c[0] += 1
        if logged_at.astimezone(tz) >= hour_start:
            c[1] += 1
    out = []
    for s in senders:
        row = outreach_row(db, s)
        today, hour = counts.get(s.address.lower(), [0, 0])
        out.append(Mailbox(
            sender=s, row=row, email=s.address.lower(), enabled=row.enabled, stage=row.stage or "Stage 1",
            daily_limit=row.daily_limit if row.daily_limit is not None else settings.default_daily_limit,
            hourly_limit=row.hourly_limit if row.hourly_limit is not None else settings.default_hourly_limit,
            sent_today=today, sent_this_hour=hour,
            last_sent_at=row.last_sent_at.astimezone(tz) if row.last_sent_at else None,
            next_eligible_at=row.next_eligible_at.astimezone(tz) if row.next_eligible_at else None,
            paused=row.paused, has_credentials=has_credentials(s)))
    return out


def mailbox_block_reason(m: Mailbox, now: datetime, minimum_gap_minutes: int) -> str | None:
    if not m.enabled:
        return "Disabled"
    if m.paused:
        return "Paused"
    if m.sent_today >= m.daily_limit:
        return "Daily limit reached"
    if m.sent_this_hour >= m.hourly_limit:
        return "Hourly limit reached"
    if not m.has_credentials:
        return "Not connected"
    if m.last_sent_at and now < m.last_sent_at + timedelta(minutes=minimum_gap_minutes):
        return "Waiting for the sending gap"
    # A zero gap disables all time-gap checks, including old next-eligible values.
    if minimum_gap_minutes > 0 and m.next_eligible_at and now < m.next_eligible_at:
        return "Not yet eligible"
    return None


def select_mailbox(mailboxes: list[Mailbox], now: datetime, minimum_gap_minutes: int) -> Mailbox | None:
    eligible = [m for m in mailboxes if mailbox_block_reason(m, now, minimum_gap_minutes) is None]
    if not eligible:
        return None
    return min(eligible, key=lambda m: (m.sent_today / max(m.daily_limit, 1),
                                         m.last_sent_at or datetime.min.replace(tzinfo=now.tzinfo), m.email))


# --------------------------------------------------------------------------- settings / state
def settings_for(db: Session, brand_id: int) -> OutreachSettings:
    s = db.get(OutreachSettings, brand_id)
    if s is None:
        brand = db.get(Brand, brand_id)
        s = OutreachSettings(brand_id=brand_id, enabled=True, emergency_stop=True, default_hourly_limit=1,
                             default_daily_limit=3, minimum_gap_minutes=25,
                             cc_email=(brand.master_email or "") if brand else "", sending_start_time="10:00",
                             sending_end_time="18:00", brand_facts="", website="", phone="",
                             signature_name="Ajay Veer", signature_title="Sales Executive", run_order=brand_id)
        db.add(s)
        db.flush()
    return s


def global_state(db: Session) -> OutreachGlobal:
    g = db.get(OutreachGlobal, 1)
    if g is None:
        g = OutreachGlobal(id=1, next_start_index=0, cross_brand_duplicate_days=0, max_drafts_per_brand_per_run=20,
                           openai_model="gpt-5-mini", reports_enabled=False,
                           report_recipient="info@99marketingservices.com", report_sender="linkwithmarketing@gmail.com",
                           last_run_summary="")
        db.add(g)
        db.flush()
    return g


def brand_order(db: Session) -> list[int]:
    rows = db.execute(select(Brand.id, OutreachSettings.run_order).outerjoin(OutreachSettings, OutreachSettings.brand_id == Brand.id)
                      .where(Brand.status == "active")).all()
    return [bid for bid, _ in sorted(rows, key=lambda r: (r[1] if r[1] is not None else r[0], r[0]))]


# --------------------------------------------------------------------------- results
@dataclass
class BrandRunResult:
    brand_id: int
    brand: str = ""
    processed: bool = False
    generated_count: int = 0
    sent: bool = False
    skipped_reason: str = ""
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"brand_id": self.brand_id, "brand": self.brand, "processed": self.processed,
                "generated": self.generated_count, "sent": self.sent, "skipped": self.skipped_reason,
                "notes": self.notes, "errors": self.errors}


# --------------------------------------------------------------------------- brand runner
class BrandRunner:
    def __init__(self, db: Session, brand: Brand, g: OutreachGlobal, *, deadline: float | None = None,
                 now: datetime | None = None, generate=None):
        self.db = db
        self.brand = brand
        self.g = g
        self.settings = settings_for(db, brand.id)
        self.tz = ZoneInfo(brand.timezone or "Asia/Kolkata")
        self.now = (now or datetime.now(timezone.utc)).astimezone(self.tz)
        self.deadline = deadline
        if generate is None:
            key = generator.openai_key(db)

            def generate(*a, _key=key):
                return generator.generate_email(*a, key=_key)
        self.generate = generate

    # --- queries standing in for the Send Log / Suppression tab reads
    def _suppressed(self, email: str) -> bool:
        return db_exists(self.db, select(OutreachSuppression.id).where(OutreachSuppression.brand_id == self.brand.id,
                                                                       OutreachSuppression.email == email))

    def _already_contacted(self, email: str) -> bool:
        return db_exists(self.db, select(OutreachSendLog.id).where(OutreachSendLog.brand_id == self.brand.id,
                                                                   OutreachSendLog.lead_email == email,
                                                                   OutreachSendLog.result.in_(COUNTED)))

    def _recent_cross_brand(self, email: str) -> bool:
        days = self.g.cross_brand_duplicate_days
        if days <= 0:
            return False
        return db_exists(self.db, select(OutreachSendLog.id).where(
            OutreachSendLog.lead_email == email, OutreachSendLog.result.in_(COUNTED),
            OutreachSendLog.logged_at >= self.now - timedelta(days=days)))

    def _remaining_gap_minutes(self) -> int:
        last = self.db.scalar(select(func.max(OutreachSendLog.logged_at)).where(
            OutreachSendLog.brand_id == self.brand.id, OutreachSendLog.result.in_(COUNTED)))
        if last is None:
            return 0
        remaining = (last + timedelta(minutes=self.settings.minimum_gap_minutes) - self.now).total_seconds()
        return 0 if remaining <= 0 else int((remaining + 59) // 60)

    def _set_lead(self, lead: OutreachLead, status: str, error: str, dry_run: bool) -> None:
        if not dry_run:
            lead.status, lead.error = status, error

    def process(self, *, dry_run: bool = False, generate_only: bool = False) -> BrandRunResult:
        r = BrandRunResult(brand_id=self.brand.id, brand=self.brand.name, processed=True)
        s = self.settings
        if not s.enabled:
            r.skipped_reason = "brand disabled"
            return r
        if s.emergency_stop:
            r.skipped_reason = "Emergency stop active"
            return r
        if generate_only:
            r.generated_count = self.generate_drafts(dry_run=False, result=r)
            r.skipped_reason = "Generate-only mode complete; Gmail sending disabled"
            return r

        r.generated_count = self.generate_drafts(dry_run, r)

        lead = self.db.scalars(select(OutreachLead).where(
            OutreachLead.brand_id == self.brand.id, func.lower(func.trim(OutreachLead.approval)) == "yes",
            OutreachLead.status.in_(SENDABLE_STATUSES)).order_by(OutreachLead.id).limit(1)).first()
        if lead is None:
            r.skipped_reason = "No approved lead is ready"
            return r

        remaining = self._remaining_gap_minutes()
        if remaining:
            r.skipped_reason = f"Brand send gap active: {remaining} minute(s) remaining"
            return r

        recipient, note = normalize_email_address(lead.email)
        subject, body = (lead.subject or "").strip(), (lead.body or "").strip()
        if not recipient or not is_valid_email(recipient):
            self._set_lead(lead, "Skipped", "Invalid email address", dry_run)
            r.skipped_reason = "Invalid email address"
            return r
        if not subject or not body:
            r.skipped_reason = "Approved row has blank subject or body"
            self._set_lead(lead, "Scheduled", r.skipped_reason, dry_run)
            return r
        if self._suppressed(recipient):
            self._set_lead(lead, "Unsubscribed", "Recipient suppressed", dry_run)
            r.skipped_reason = "Recipient suppressed"
            return r
        if db_exists(self.db, select(OutreachSendLog.id).where(OutreachSendLog.lead_id == lead.id,
                                                               OutreachSendLog.result == "Sending")):
            self._set_lead(lead, "Skipped", "An earlier send attempt did not finish. Check the mailbox's Sent folder "
                                            "before approving this lead again.", dry_run)
            r.skipped_reason = "Delivery of an earlier attempt is uncertain"
            return r
        if self._already_contacted(recipient):
            self._set_lead(lead, "Skipped", "Recipient already contacted by this brand", dry_run)
            r.skipped_reason = "Brand duplicate recipient"
            return r
        if self._recent_cross_brand(recipient):
            self._set_lead(lead, "Skipped", "Recipient recently contacted by another brand", dry_run)
            r.skipped_reason = "Cross-brand duplicate recipient"
            return r
        if not valid_send_time(self.now, s.sending_start_time, s.sending_end_time):
            self._set_lead(lead, "Scheduled", "Outside sending hours", dry_run)
            r.skipped_reason = "Outside sending hours"
            return r

        mailboxes = load_mailboxes(self.db, self.brand.id, s, self.now)
        mailbox = select_mailbox(mailboxes, self.now, s.minimum_gap_minutes)
        if mailbox is None:
            self._set_lead(lead, "Scheduled", "No eligible mailbox", dry_run)
            r.skipped_reason = "No eligible mailbox"
            return r
        if dry_run:
            r.notes.append(f"Would send lead #{lead.id} from {mailbox.email} to {recipient}; cc configured={bool(s.cc_email)}.")
            return r
        if note:
            lead.email = recipient
        self._send(lead, mailbox, recipient, subject, body, r)
        return r

    def generate_drafts(self, dry_run: bool, result: BrandRunResult) -> int:
        generated = attempts = 0
        leads = self.db.scalars(select(OutreachLead).where(
            OutreachLead.brand_id == self.brand.id,
            OutreachLead.status.notin_(TERMINAL_GENERATION_STATUSES),
            (func.trim(OutreachLead.subject) == "") | (func.trim(OutreachLead.body) == ""))
            .order_by(OutreachLead.id)).all()
        for lead in leads:
            if attempts >= self.g.max_drafts_per_brand_per_run:
                break
            if self.deadline is not None and _time.monotonic() > self.deadline:
                result.notes.append("Draft writing paused for this run (time limit); it continues next run.")
                break
            recipient, _ = normalize_email_address(lead.email)
            if not lead.person_name and not recipient:
                continue
            if recipient and not is_valid_email(recipient):
                self._set_lead(lead, "Skipped", "Invalid email address", dry_run)
                continue
            if not can_generate(lead):
                continue
            attempts += 1
            if dry_run:
                result.notes.append(f"Would generate draft for lead #{lead.id}.")
                continue
            try:
                subject, body = self.generate(self.g.openai_model or "gpt-5-mini", self.brand.name, self.settings,
                                              (lead.person_name or "").strip(), (lead.details or "").strip())
                lead.subject, lead.body, lead.status, lead.error = subject, body, "Generated", ""
                generated += 1
            except Exception as error:  # noqa: BLE001 - recorded on the lead exactly like the Sheet
                lead.status, lead.error = "Failed", f"OpenAI generation error: {error}"[:2000]
            self.db.commit()
        return generated

    def _send(self, lead: OutreachLead, m: Mailbox, recipient: str, subject: str, body: str, r: BrandRunResult) -> None:
        db, s = self.db, self.settings
        # Claim first: from here on the lead is never emailed twice, whatever happens next.
        claim = OutreachSendLog(brand_id=self.brand.id, lead_id=lead.id, logged_at=self.now, sender_email=m.email,
                                lead_email=recipient, subject=subject, result="Sending", error="",
                                daily_limit=m.daily_limit, stage=m.stage)
        db.add(claim)
        db.commit()
        cc = (s.cc_email or "").strip()
        req = SendRequest(channel="email", from_address=m.sender.address, from_name=None, to_address=recipient,
                          cc=cc or None, subject=subject, body=body, plain_text=True,
                          idempotency_key=f"outreach-{claim.id}", secret_ref=m.sender.secret_ref,
                          sender_config=m.sender.provider_config or {})
        try:
            res = get_provider("email", m.sender.provider).send(req)
        except Exception as exc:  # noqa: BLE001
            res = None
            error_text = f"Send failed: {exc}"
        else:
            error_text = "" if res.ok else f"Send failed: {res.error}"
        if res is not None and res.ok:
            message_id = res.provider_message_id or ""
            claim.result, claim.gmail_message_id, claim.gmail_thread_id = "Sent", message_id, res.thread_id or ""
            lead.status, lead.sent_at, lead.error, lead.sent_from = "Sent", self.now, "", m.email
            lead.approval = (lead.approval or "").strip() or "Yes"
            lead.gmail_message_id, lead.gmail_thread_id, lead.follow_up_status = message_id, res.thread_id or "", "Pending"
            m.row.last_sent_at = self.now
            m.row.next_eligible_at = self.now + timedelta(minutes=s.minimum_gap_minutes)
            m.row.last_result = f"Sent lead #{lead.id} to {recipient}"[:255]
            m.sender.last_error = None
            r.sent = True
        else:
            claim.result, claim.error = "Failed", error_text[:2000]
            lead.status, lead.error = "Failed", error_text[:2000]
            m.row.last_result = error_text[:255]
            r.errors.append(error_text)
        db.commit()


def db_exists(db: Session, stmt) -> bool:
    return db.scalar(select(stmt.exists())) is True


# --------------------------------------------------------------------------- unified run
class RunLocked(RuntimeError):
    pass


def run_unified(db: Session, *, selected_brand_id: int | None = None, dry_run: bool = False,
                generate_only: bool = False, now: datetime | None = None, generate=None,
                time_budget: float | None = GENERATION_BUDGET_SECONDS) -> list[dict]:
    """One pass over all brands (the Cloud Run job's main.py). Returns one result per brand."""
    lock_conn = get_engine().connect()
    try:
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": RUN_LOCK_KEY}).scalar():
            raise RunLocked("Another outreach run is already active")
        try:
            return _run(db, selected_brand_id, dry_run, generate_only, now, generate, time_budget)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": RUN_LOCK_KEY})
    finally:
        lock_conn.close()


def _run(db, selected_brand_id, dry_run, generate_only, now, generate, time_budget) -> list[dict]:
    g = global_state(db)
    order = brand_order(db)
    if selected_brand_id is not None:
        ordered = [selected_brand_id]
    else:
        start = g.next_start_index % len(order) if order else 0
        ordered = order[start:] + order[:start]
    deadline = _time.monotonic() + time_budget if time_budget else None
    results: list[BrandRunResult] = []
    for bid in ordered:
        brand = db.get(Brand, bid)
        if brand is None:
            continue
        try:
            runner = BrandRunner(db, brand, g, deadline=deadline, now=now, generate=generate)
            results.append(runner.process(dry_run=dry_run, generate_only=generate_only))
            if dry_run:
                db.rollback()
            else:
                db.commit()
        except Exception as error:  # noqa: BLE001 - one brand never stops the others
            db.rollback()
            results.append(BrandRunResult(brand_id=bid, brand=brand.name, processed=False, errors=[f"{brand.name} failed: {error}"]))
    if not dry_run:
        g = global_state(db)
        if selected_brand_id is None and order:
            g.next_start_index = (g.next_start_index + 1) % len(order)
        g.last_run_at = datetime.now(timezone.utc)
        g.last_run_summary = "; ".join(
            f"{x.brand}: {'sent' if x.sent else (x.skipped_reason or ('error' if x.errors else '-'))}" for x in results)[:4000]
        db.commit()
    return [x.as_dict() for x in results]
