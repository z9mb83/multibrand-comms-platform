from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import BrandCtx, accessible_brand_ids, brand_ctx, current_user
from app.core.db import get_db
from app.models import Campaign, DailyTask, FollowUpRule, FollowUpTask, User
from app.models.enums import Role, TaskStatus
from app.schemas.api import ApproveFollowUpIn, FollowUpRuleIn, FollowUpRuleOut, FollowUpTaskOut, TaskIn, TaskOut, TaskUpdate
from app.services import followups
from app.services.queue import enqueue_dispatch
from app.services.tasks import update_status

router = APIRouter(prefix="/brands/{brand_id}", tags=["follow-ups & tasks"])


@router.get("/follow-up-rules", response_model=list[FollowUpRuleOut])
def list_rules(ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    return db.scalars(select(FollowUpRule).where(FollowUpRule.brand_id == ctx.brand_id)).all()


@router.post("/follow-up-rules", response_model=FollowUpRuleOut, status_code=201)
def create_rule(body: FollowUpRuleIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.MANAGER)
    if body.campaign_id:
        c = db.get(Campaign, body.campaign_id)
        if c is None or c.brand_id != ctx.brand_id:
            raise HTTPException(400, "campaign does not belong to this brand")
    for s in body.steps:
        if s.get("mode", "scheduled") not in ("manual", "scheduled", "reminder", "approval_send"):
            raise HTTPException(400, "invalid step mode")
    r = FollowUpRule(brand_id=ctx.brand_id, **body.model_dump())
    db.add(r)
    db.commit()
    return r


@router.get("/follow-ups", response_model=list[FollowUpTaskOut])
def list_followups(status: str | None = None, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    q = select(FollowUpTask).where(FollowUpTask.brand_id == ctx.brand_id)
    if status:
        q = q.where(FollowUpTask.status == status)
    return db.scalars(q.order_by(FollowUpTask.due_at).limit(500)).all()


@router.post("/follow-ups/{task_id}/approve")
def approve_followup(task_id: int, body: ApproveFollowUpIn, ctx: BrandCtx = Depends(brand_ctx),
                     db: Session = Depends(get_db)):
    ctx.require(Role.MANAGER)
    t = db.get(FollowUpTask, task_id)
    if t is None or t.brand_id != ctx.brand_id:
        raise HTTPException(404, "follow-up not found")
    msg = followups.approve_and_send(db, task_id, ctx.user.id, body.body, body.subject)
    db.commit()
    enqueue_dispatch([msg.id])
    return {"message_id": msg.id}


@router.post("/follow-ups/{task_id}/pause")
def pause_followup(task_id: int, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    t = db.get(FollowUpTask, task_id)
    if t is None or t.brand_id != ctx.brand_id:
        raise HTTPException(404, "follow-up not found")
    t.status = "paused"
    db.commit()
    return {"ok": True}


def _task_out(t: DailyTask) -> TaskOut:
    return TaskOut.model_validate(t)


@router.get("/tasks", response_model=list[TaskOut])
def brand_tasks(status: str | None = None, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    q = select(DailyTask).where(DailyTask.brand_id == ctx.brand_id)
    if status:
        q = q.where(DailyTask.status == status)
    return [_task_out(t) for t in db.scalars(q.order_by(DailyTask.due_at.nulls_last()).limit(500))]


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(body: TaskIn, ctx: BrandCtx = Depends(brand_ctx), db: Session = Depends(get_db)):
    ctx.require(Role.AGENT)
    t = DailyTask(brand_id=ctx.brand_id, created_by=ctx.user.id, source="manual", status=TaskStatus.PENDING,
                  **body.model_dump())
    db.add(t)
    db.commit()
    return _task_out(t)


tasks_global = APIRouter(prefix="/tasks", tags=["follow-ups & tasks"])


@tasks_global.get("", response_model=list[TaskOut])
def my_board(day: str | None = None, mine: bool = False, status: str | None = None,
             user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Daily task dashboard across all accessible brands."""
    ids = accessible_brand_ids(db, user)
    if not ids:
        return []
    q = select(DailyTask).where(DailyTask.brand_id.in_(ids))
    if mine:
        q = q.where(DailyTask.assigned_user_id == user.id)
    if status:
        q = q.where(DailyTask.status == status)
    if day:
        from app.services.common import local_tz
        start = datetime.fromisoformat(day).replace(tzinfo=local_tz())
        from datetime import timedelta
        q = q.where(DailyTask.due_at >= start, DailyTask.due_at < start + timedelta(days=1))
    return [_task_out(t) for t in db.scalars(q.order_by(DailyTask.due_at.nulls_last()).limit(500))]


@tasks_global.patch("/{task_id}", response_model=TaskOut)
def patch_task(task_id: int, body: TaskUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(DailyTask, task_id)
    if t is None or t.brand_id not in accessible_brand_ids(db, user):
        raise HTTPException(404, "task not found")
    data = body.model_dump(exclude_unset=True)
    status = data.pop("status", None)
    notes = data.pop("notes", None)
    for k, v in data.items():
        setattr(t, k, v)
    if status:
        update_status(db, t, status, user.id, notes)
    elif notes:
        t.notes = f"{t.notes}\n{notes}" if t.notes else notes
    db.commit()
    return _task_out(t)
