from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import accessible_brand_ids, current_user, require_super_admin
from app.core.db import get_db
from app.models import (
    Brand,
    Campaign,
    Conversation,
    DailyTask,
    FollowUpTask,
    InboundMessage,
    MasterReport,
    NotificationDelivery,
    OutboundMessage,
    SenderAccount,
    User,
)
from app.schemas.api import DeliveryOut, MasterSettingsIO
from app.services import reports as svc
from app.services.common import audit, local_tz, now_utc

router = APIRouter(prefix="/master", tags=["master control"])


@router.get("/settings", response_model=MasterSettingsIO)
def get_settings(_: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    s = svc.get_settings_row(db)
    db.commit()
    return s


@router.put("/settings", response_model=MasterSettingsIO)
def put_settings(body: MasterSettingsIO, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    s = svc.get_settings_row(db)
    for field in ("notification_email_sender_id", "notification_whatsapp_sender_id", "notification_sms_sender_id"):
        sid = getattr(body, field)
        if sid:
            acc = db.get(SenderAccount, sid)
            if acc is None or not acc.is_notification_sender:
                raise HTTPException(400, f"{field} must reference a sender flagged as a notification sender")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    audit(db, "master.settings_updated", user_id=admin.id)
    db.commit()
    return s


@router.get("/reports")
def list_reports(_: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    reps = db.scalars(select(MasterReport).order_by(MasterReport.report_date.desc(), MasterReport.kind).limit(60)).all()
    return [{"id": r.id, "kind": r.kind, "report_date": r.report_date, "status": r.status,
             "generated_at": r.generated_at, "overall": r.data.get("overall"),
             "action_items": r.data.get("action_items")} for r in reps]


class GenerateIn(BaseModel):
    kind: str = "morning"
    report_date: date | None = None
    deliver: bool = False


@router.post("/reports/generate")
def generate(body: GenerateIn, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    d = body.report_date or now_utc().astimezone(local_tz()).date()
    r = svc.generate(db, body.kind, d, force=True)
    stats = svc.deliver_pending(db) if body.deliver else None
    audit(db, "report.generated_manually", user_id=admin.id, entity_type="master_report", entity_id=r.id)
    db.commit()
    return {"id": r.id, "status": r.status, "data": r.data, "delivery": stats}


@router.get("/reports/{report_id}/html", response_class=HTMLResponse)
def report_html(report_id: int, _: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    r = db.get(MasterReport, report_id)
    if r is None:
        raise HTTPException(404)
    return r.html


@router.get("/reports/{report_id}/xlsx")
def report_xlsx(report_id: int, _: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    r = db.get(MasterReport, report_id)
    if r is None:
        raise HTTPException(404)
    return Response(svc.spreadsheet(r.data),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{r.kind}-{r.report_date}.xlsx"'})


@router.get("/deliveries", response_model=list[DeliveryOut])
def deliveries(_: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    return db.scalars(select(NotificationDelivery).order_by(NotificationDelivery.id.desc()).limit(100)).all()


analytics = APIRouter(prefix="/analytics", tags=["analytics"])


@analytics.get("/overview")
def overview(brand_id: int | None = None, days: int = 7, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    ids = accessible_brand_ids(db, user)
    if brand_id is not None:
        if brand_id not in ids:
            raise HTTPException(403, "No access to this brand")
        ids = {brand_id}
    if not ids:
        return {"brands": [], "totals": {}, "daily": []}
    since = now_utc() - timedelta(days=days)
    sent = dict(db.execute(select(OutboundMessage.brand_id, func.count()).where(
        OutboundMessage.brand_id.in_(ids), OutboundMessage.status.in_(svc.SENT), OutboundMessage.sent_at >= since)
        .group_by(OutboundMessage.brand_id)).all())
    failed = dict(db.execute(select(OutboundMessage.brand_id, func.count()).where(
        OutboundMessage.brand_id.in_(ids), OutboundMessage.status.in_(["failed", "held_for_review", "unknown"]))
        .group_by(OutboundMessage.brand_id)).all())
    queued = dict(db.execute(select(OutboundMessage.brand_id, func.count()).where(
        OutboundMessage.brand_id.in_(ids), OutboundMessage.status == "queued").group_by(OutboundMessage.brand_id)).all())
    replies = dict(db.execute(select(InboundMessage.brand_id, func.count()).where(
        InboundMessage.brand_id.in_(ids), InboundMessage.received_at >= since).group_by(InboundMessage.brand_id)).all())
    unanswered = dict(db.execute(select(Conversation.brand_id, func.count()).where(
        Conversation.brand_id.in_(ids), Conversation.status == "needs_reply").group_by(Conversation.brand_id)).all())
    open_tasks = dict(db.execute(select(DailyTask.brand_id, func.count()).where(
        DailyTask.brand_id.in_(ids), DailyTask.status.in_(["Pending", "In Progress", "Overdue", "Blocked"]))
        .group_by(DailyTask.brand_id)).all())
    fu_due = dict(db.execute(select(FollowUpTask.brand_id, func.count()).where(
        FollowUpTask.brand_id.in_(ids), FollowUpTask.status.in_(["pending", "awaiting_approval"]),
        FollowUpTask.due_at <= now_utc() + timedelta(days=1))
        .group_by(FollowUpTask.brand_id)).all())
    running = dict(db.execute(select(Campaign.brand_id, func.count()).where(
        Campaign.brand_id.in_(ids), Campaign.status.in_(["running", "approved"])).group_by(Campaign.brand_id)).all())
    senders = dict(db.execute(select(SenderAccount.brand_id, func.count()).where(
        SenderAccount.brand_id.in_(ids), SenderAccount.status == "active",
        SenderAccount.connection_status == "connected").group_by(SenderAccount.brand_id)).all())
    daily = db.execute(select(func.date(OutboundMessage.sent_at), OutboundMessage.channel, func.count()).where(
        OutboundMessage.brand_id.in_(ids), OutboundMessage.status.in_(svc.SENT), OutboundMessage.sent_at >= since)
        .group_by(func.date(OutboundMessage.sent_at), OutboundMessage.channel)).all()
    brands = db.scalars(select(Brand).where(Brand.id.in_(ids)).order_by(Brand.name)).all()
    rows = [{"brand_id": b.id, "brand": b.name, "sent": sent.get(b.id, 0), "failed_or_held": failed.get(b.id, 0),
             "queued": queued.get(b.id, 0), "replies": replies.get(b.id, 0), "unanswered": unanswered.get(b.id, 0),
             "open_tasks": open_tasks.get(b.id, 0), "follow_ups_due": fu_due.get(b.id, 0),
             "running_campaigns": running.get(b.id, 0), "active_senders": senders.get(b.id, 0)} for b in brands]
    keys = [k for k in rows[0] if k not in ("brand_id", "brand")] if rows else []
    return {"brands": rows, "totals": {k: sum(r[k] for r in rows) for k in keys},
            "daily": [{"date": str(d), "channel": ch, "count": c} for d, ch, c in daily]}
