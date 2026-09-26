"""Email outreach API: the Sheet's tabs as endpoints, plus run/preview controls."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import BrandCtx, brand_ctx, require_super_admin
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Brand, OutreachLead, OutreachReportReceipt, OutreachSendLog, SenderAccount, User
from app.models.enums import Role
from app.models.outreach import OutreachSuppression
from app.services.common import audit
from app.services.outreach import engine
from app.services.outreach import reports as outreach_reports
from app.services.outreach.sheet_import import import_file

router = APIRouter(prefix="/brands/{brand_id}/outreach", tags=["outreach"])
global_router = APIRouter(prefix="/outreach", tags=["outreach"])
MAX_BYTES = 20 * 1024 * 1024
TIME_RE = r"^([01]\d|2[0-3]):[0-5]\d$"


# ------------------------------------------------------------------ schemas
class SettingsIn(BaseModel):
    enabled: bool | None = None
    emergency_stop: bool | None = None
    default_hourly_limit: int | None = Field(default=None, ge=1, le=500)
    default_daily_limit: int | None = Field(default=None, ge=1, le=2000)
    minimum_gap_minutes: int | None = Field(default=None, ge=0, le=1440)
    cc_email: str | None = None
    sending_start_time: str | None = Field(default=None, pattern=TIME_RE)
    sending_end_time: str | None = Field(default=None, pattern=TIME_RE)
    brand_facts: str | None = Field(default=None, max_length=4000)
    website: str | None = None
    phone: str | None = None
    signature_name: str | None = None
    signature_title: str | None = None
    run_order: int | None = None


class LeadIn(BaseModel):
    person_name: str = ""
    email: str
    details: str = ""


class LeadPatch(BaseModel):
    person_name: str | None = None
    email: str | None = None
    details: str | None = None
    subject: str | None = None
    body: str | None = None
    status: str | None = None
    approval: str | None = None
    follow_up_status: str | None = None


class ApproveIn(BaseModel):
    ids: list[int]
    approval: str = "Yes"


class MailboxPatch(BaseModel):
    enabled: bool | None = None
    stage: str | None = None
    daily_limit: int | None = Field(default=None, ge=0, le=2000)
    hourly_limit: int | None = Field(default=None, ge=0, le=500)
    clear_limits: bool = False
    paused: bool | None = None
    pause_reason: str | None = None
    clean_days: int | None = Field(default=None, ge=0)


class SuppressionIn(BaseModel):
    email: str
    reason: str = ""


class GlobalIn(BaseModel):
    cross_brand_duplicate_days: int | None = Field(default=None, ge=0, le=3650)
    max_drafts_per_brand_per_run: int | None = Field(default=None, ge=0, le=100)
    openai_model: str | None = None
    reports_enabled: bool | None = None
    report_recipient: str | None = None
    report_sender: str | None = None


# ------------------------------------------------------------------ serializers
def settings_out(s) -> dict:
    return {k: getattr(s, k) for k in ("enabled", "emergency_stop", "default_hourly_limit", "default_daily_limit",
                                       "minimum_gap_minutes", "cc_email", "sending_start_time", "sending_end_time",
                                       "brand_facts", "website", "phone", "signature_name", "signature_title", "run_order")}


def lead_out(x: OutreachLead) -> dict:
    return {"id": x.id, "person_name": x.person_name, "email": x.email, "details": x.details, "subject": x.subject,
            "body": x.body, "status": x.status, "sent_at": x.sent_at, "error": x.error, "sent_from": x.sent_from,
            "approval": x.approval, "gmail_message_id": x.gmail_message_id, "follow_up_status": x.follow_up_status,
            "created_at": x.created_at}


def global_out(g) -> dict:
    s = get_settings()
    return {"cross_brand_duplicate_days": g.cross_brand_duplicate_days,
            "max_drafts_per_brand_per_run": g.max_drafts_per_brand_per_run, "openai_model": g.openai_model,
            "reports_enabled": g.reports_enabled, "report_recipient": g.report_recipient,
            "report_sender": g.report_sender, "last_run_at": g.last_run_at, "last_run_summary": g.last_run_summary,
            "openai_configured": bool(s.openai_api_key), "live_sending": s.provider_mode != "mock"}


def _lead(db: Session, brand_id: int, lead_id: int) -> OutreachLead:
    x = db.get(OutreachLead, lead_id)
    if x is None or x.brand_id != brand_id:
        raise HTTPException(404, "lead not found")
    return x


def _run(db: Session, **kw) -> list[dict]:
    try:
        return engine.run_unified(db, **kw)
    except engine.RunLocked as exc:
        raise HTTPException(409, str(exc)) from exc


# ------------------------------------------------------------------ brand: overview + settings
@router.get("")
def overview(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    s = engine.settings_for(db, ctx.brand_id)
    brand = db.get(Brand, ctx.brand_id)
    tz = ZoneInfo(brand.timezone or "Asia/Kolkata")
    now = datetime.now(timezone.utc).astimezone(tz)
    counts = dict(db.execute(select(OutreachLead.status, func.count()).where(OutreachLead.brand_id == ctx.brand_id)
                             .group_by(OutreachLead.status)).all())
    approved_ready = db.scalar(select(func.count()).where(
        OutreachLead.brand_id == ctx.brand_id, func.lower(func.trim(OutreachLead.approval)) == "yes",
        OutreachLead.status.in_(engine.SENDABLE_STATUSES)))
    sent_today = db.scalar(select(func.count()).where(
        OutreachSendLog.brand_id == ctx.brand_id, OutreachSendLog.result == "Sent",
        OutreachSendLog.logged_at >= now.replace(hour=0, minute=0, second=0, microsecond=0)))
    db.commit()
    g = engine.global_state(db)
    db.commit()
    return {"settings": settings_out(s), "counts": counts, "approved_ready": approved_ready, "sent_today": sent_today,
            "within_hours": engine.valid_send_time(now, s.sending_start_time, s.sending_end_time),
            "local_time": now.strftime("%H:%M"), "role": ctx.role, "global": global_out(g)}


@router.put("/settings")
def update_settings(body: SettingsIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    s = engine.settings_for(db, ctx.brand_id)
    data = body.model_dump(exclude_unset=True)
    if "cc_email" in data and data["cc_email"] and not engine.is_valid_email(data["cc_email"]):
        raise HTTPException(400, "CC email is not a valid address")
    for k, v in data.items():
        setattr(s, k, v.strip() if isinstance(v, str) else v)
    audit(db, "outreach.settings_updated", user_id=ctx.user.id, brand_id=ctx.brand_id, **{k: str(v)[:80] for k, v in data.items()})
    db.commit()
    return settings_out(s)


# ------------------------------------------------------------------ leads
@router.get("/leads")
def list_leads(status: str | None = None, q: str | None = None, limit: int = Query(100, le=1000), offset: int = 0,
               ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    stmt = select(OutreachLead).where(OutreachLead.brand_id == ctx.brand_id)
    if status == "Approved-ready":
        stmt = stmt.where(func.lower(func.trim(OutreachLead.approval)) == "yes", OutreachLead.status.in_(engine.SENDABLE_STATUSES))
    elif status == "Needs approval":
        stmt = stmt.where(OutreachLead.status == "Generated", func.lower(func.trim(OutreachLead.approval)) != "yes")
    elif status:
        stmt = stmt.where(OutreachLead.status == status)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(OutreachLead.person_name).like(like), OutreachLead.email.like(like),
                              func.lower(OutreachLead.subject).like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(OutreachLead.id).limit(limit).offset(offset)).all()
    return {"total": total, "items": [lead_out(x) for x in rows]}


@router.post("/leads", status_code=201)
def add_lead(body: LeadIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.AGENT)
    email, _ = engine.normalize_email_address(body.email)
    if not engine.is_valid_email(email):
        raise HTTPException(400, "Enter a valid email address")
    x = OutreachLead(brand_id=ctx.brand_id, person_name=body.person_name.strip(), email=email, details=body.details.strip(),
                     subject="", body="", status="Pending", error="", sent_from="", approval="", gmail_message_id="",
                     gmail_thread_id="", follow_up_status="", created_by=ctx.user.id)
    db.add(x)
    db.commit()
    return lead_out(x)


@router.patch("/leads/{lead_id}")
def edit_lead(lead_id: int, body: LeadPatch, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.AGENT)
    x = _lead(db, ctx.brand_id, lead_id)
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in engine.STATUS_VALUES:
        raise HTTPException(400, "invalid status")
    if "follow_up_status" in data and data["follow_up_status"] not in engine.FOLLOW_UP_VALUES + [""]:
        raise HTTPException(400, "invalid follow-up status")
    if "approval" in data:
        ctx.require(Role.MANAGER)
        data["approval"] = "Yes" if engine.yes(data["approval"]) else ""
    if x.status == "Sent" and any(k in data for k in ("email", "subject", "body")):
        raise HTTPException(400, "This lead was already emailed; its message can't be changed")
    if "email" in data:
        data["email"], _ = engine.normalize_email_address(data["email"])
    for k, v in data.items():
        setattr(x, k, v)
    db.commit()
    return lead_out(x)


@router.post("/leads/approve")
def approve(body: ApproveIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.MANAGER)
    value = "Yes" if engine.yes(body.approval) else ""
    rows = db.scalars(select(OutreachLead).where(OutreachLead.brand_id == ctx.brand_id, OutreachLead.id.in_(body.ids))).all()
    for x in rows:
        if x.status != "Sent":
            x.approval = value
    audit(db, "outreach.approved" if value else "outreach.unapproved", user_id=ctx.user.id, brand_id=ctx.brand_id, count=len(rows))
    db.commit()
    return {"updated": len(rows)}


@router.delete("/leads/{lead_id}", status_code=204)
def delete_lead(lead_id: int, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.MANAGER)
    db.delete(_lead(db, ctx.brand_id, lead_id))
    db.commit()


@router.post("/import")
async def import_tab(kind: str = Form(...), file: UploadFile = File(...), ctx: BrandCtx = Depends(brand_ctx),
                     db: Session = Depends(get_db)):
    ctx.require(Role.MANAGER)
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File is larger than 20 MB")
    out = import_file(db, ctx.brand_id, kind, data, file.filename or "upload.csv", ctx.user.id)
    audit(db, "outreach.imported", user_id=ctx.user.id, brand_id=ctx.brand_id, kind=kind, **{k: v for k, v in out.items() if k != "problems"})  # noqa: E501
    db.commit()
    return out


# ------------------------------------------------------------------ mailboxes
@router.get("/mailboxes")
def mailboxes(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    s = engine.settings_for(db, ctx.brand_id)
    brand = db.get(Brand, ctx.brand_id)
    now = datetime.now(timezone.utc).astimezone(ZoneInfo(brand.timezone or "Asia/Kolkata"))
    boxes = engine.load_mailboxes(db, ctx.brand_id, s, now)
    db.commit()
    return [{"sender_id": m.sender.id, "email": m.email, "is_master": m.sender.is_master, "provider": m.sender.provider,
             "connected": m.has_credentials, "enabled": m.enabled, "stage": m.stage, "daily_limit": m.daily_limit,
             "hourly_limit": m.hourly_limit, "custom_limits": m.row.daily_limit is not None or m.row.hourly_limit is not None,
             "sent_today": m.sent_today, "sent_this_hour": m.sent_this_hour, "clean_days": m.row.clean_days,
             "last_sent_at": m.last_sent_at, "next_eligible_at": m.next_eligible_at, "paused": m.paused,
             "pause_reason": m.row.pause_reason, "last_result": m.row.last_result,
             "ready": engine.mailbox_block_reason(m, now, s.minimum_gap_minutes) or "Ready"} for m in boxes]


@router.patch("/mailboxes/{sender_id}")
def edit_mailbox(sender_id: int, body: MailboxPatch, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    sender = db.get(SenderAccount, sender_id)
    if sender is None or sender.brand_id != ctx.brand_id or sender.channel != "email":
        raise HTTPException(404, "mailbox not found")
    row = engine.outreach_row(db, sender)
    data = body.model_dump(exclude_unset=True)
    if data.pop("clear_limits", False):
        row.daily_limit = row.hourly_limit = None
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ send log + suppression
@router.get("/send-log")
def send_log(limit: int = Query(200, le=2000), offset: int = 0, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    stmt = select(OutreachSendLog).where(OutreachSendLog.brand_id == ctx.brand_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(OutreachSendLog.logged_at.desc(), OutreachSendLog.id.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "items": [
        {"id": r.id, "logged_at": r.logged_at, "sender_email": r.sender_email, "lead_email": r.lead_email, "subject": r.subject,
         "gmail_message_id": r.gmail_message_id, "result": r.result, "error": r.error, "daily_limit": r.daily_limit,
         "stage": r.stage, "imported": r.imported} for r in rows]}


@router.get("/suppression")
def suppression(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    rows = db.scalars(select(OutreachSuppression).where(OutreachSuppression.brand_id == ctx.brand_id)
                      .order_by(OutreachSuppression.created_at.desc())).all()
    return [{"id": r.id, "email": r.email, "reason": r.reason, "created_at": r.created_at} for r in rows]


@router.post("/suppression", status_code=201)
def add_suppression(body: SuppressionIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.AGENT)
    email = body.email.strip().lower()
    if not engine.is_valid_email(email):
        raise HTTPException(400, "Enter a valid email address")
    if db.scalar(select(OutreachSuppression.id).where(OutreachSuppression.brand_id == ctx.brand_id, OutreachSuppression.email == email)):
        raise HTTPException(409, "Already on the suppression list")
    db.add(OutreachSuppression(brand_id=ctx.brand_id, email=email, reason=body.reason.strip()[:255]))
    audit(db, "outreach.suppressed", user_id=ctx.user.id, brand_id=ctx.brand_id, email=email)
    db.commit()
    return {"ok": True}


@router.delete("/suppression/{sid}", status_code=204)
def remove_suppression(sid: int, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.BRAND_ADMIN)
    r = db.get(OutreachSuppression, sid)
    if r is None or r.brand_id != ctx.brand_id:
        raise HTTPException(404, "not found")
    audit(db, "outreach.unsuppressed", user_id=ctx.user.id, brand_id=ctx.brand_id, email=r.email)
    db.delete(r)
    db.commit()


# ------------------------------------------------------------------ run controls (this brand)
@router.post("/generate")
def generate_now(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    """Write drafts for this brand now (the --generate-only mode). Never sends email."""
    ctx.require(Role.MANAGER)
    return _run(db, selected_brand_id=ctx.brand_id, generate_only=True)[0]


@router.post("/preview")
def preview(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    """Dry run: what the next run would do for this brand. Changes nothing."""
    return _run(db, selected_brand_id=ctx.brand_id, dry_run=True, time_budget=None)[0]


@router.post("/run")
def run_brand(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    """Run this brand now with every normal check (limits, gap, hours, approvals). Sends at most one email."""
    ctx.require(Role.BRAND_ADMIN)
    out = _run(db, selected_brand_id=ctx.brand_id)[0]
    audit(db, "outreach.manual_run", user_id=ctx.user.id, brand_id=ctx.brand_id, sent=out["sent"], skipped=out["skipped"])
    db.commit()
    return out


# ------------------------------------------------------------------ global (Super Admin)
@global_router.get("/settings")
def get_global(_: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    g = engine.global_state(db)
    db.commit()
    return global_out(g)


@global_router.put("/settings")
def put_global(body: GlobalIn, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    g = engine.global_state(db)
    data = body.model_dump(exclude_unset=True)
    for k in ("report_recipient", "report_sender"):
        if k in data:
            data[k] = (data[k] or "").strip().lower()
            if not engine.is_valid_email(data[k]):
                raise HTTPException(400, f"{k.replace('_', ' ')} is not a valid email")
    for k, v in data.items():
        setattr(g, k, v)
    audit(db, "outreach.global_updated", user_id=admin.id, **{k: str(v)[:80] for k, v in data.items()})
    db.commit()
    return global_out(g)


@global_router.post("/run")
def run_all(admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """One scheduled-style pass over every brand (rotating start). At most one email per brand."""
    results = _run(db)
    audit(db, "outreach.manual_run_all", user_id=admin.id, sent=sum(1 for r in results if r["sent"]))
    db.commit()
    return results


@global_router.get("/report-preview")
def report_preview(slot: str = Query("evening", pattern="^(morning|evening)$"), day: date | None = None,
                   _: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    run_date = day or datetime.now(timezone.utc).astimezone(outreach_reports.TZ).date()
    subject, body, report = outreach_reports.build(db, slot, run_date)
    g = engine.global_state(db)
    db.commit()
    return {"to": g.report_recipient, "from": g.report_sender, "subject": subject, "body": body,
            "sent": len(report.sent), "replies": len(report.replies), "warnings": report.warnings}


@global_router.get("/report-history")
def report_history(_: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(OutreachReportReceipt).order_by(OutreachReportReceipt.created_at.desc()).limit(60)).all()
    return [{"id": r.id, "slot": r.slot, "report_date": r.report_date, "recipient": r.recipient, "status": r.status,
             "subject": r.subject, "created_at": r.created_at, "completed_at": r.completed_at} for r in rows]
