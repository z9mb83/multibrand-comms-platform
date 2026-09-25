from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DailyTask, FollowUpTask, MasterReportSettings
from app.models.enums import TaskStatus
from app.services.common import DomainError, audit, local_tz, now_utc

OPEN = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE, TaskStatus.BLOCKED)


def update_status(db: Session, task: DailyTask, status: str, user_id: int | None, notes: str | None = None,
                  source: str = "user") -> DailyTask:
    valid = {s.value for s in TaskStatus}
    if status not in valid:
        raise DomainError(f"status must be one of {sorted(valid)}")
    if status == TaskStatus.COMPLETED:
        # Only report work as completed when recorded by the system or confirmed by an authorised user
        if source == "user" and not user_id:
            raise DomainError("completion must be confirmed by an authorised user", 403)
        task.completed_at = now_utc()
        task.completed_by = user_id if source == "user" else None
        task.completed_source = source
        if task.follow_up_task_id:
            fu = db.get(FollowUpTask, task.follow_up_task_id)
            if fu and fu.status == "task_created":
                fu.status, fu.completed_at = "completed", task.completed_at
    else:
        task.completed_at = task.completed_by = task.completed_source = None
    task.status = status
    if notes:
        task.notes = f"{task.notes}\n{notes}" if task.notes else notes
    audit(db, "task.status", user_id=user_id, brand_id=task.brand_id, entity_type="daily_task", entity_id=task.id,
          status=status)
    return task


def mark_overdue(db: Session, now: datetime | None = None) -> int:
    now = now or now_utc()
    rows = db.scalars(select(DailyTask).where(DailyTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
                                              DailyTask.due_at.is_not(None), DailyTask.due_at < now)).all()
    for t in rows:
        t.status = TaskStatus.OVERDUE
    return len(rows)


def next_working_day(d: datetime, working_days: list[int]) -> datetime:
    nxt = d + timedelta(days=1)
    for _ in range(7):
        if nxt.weekday() in working_days:
            return nxt
        nxt += timedelta(days=1)
    return nxt


def carry_forward(db: Session, now: datetime | None = None) -> int:
    """Move incomplete tasks due before today to the next working day (keeps history via carried_from)."""
    settings = db.get(MasterReportSettings, 1)
    if settings is not None and not settings.task_carry_forward:
        return 0
    working = settings.working_days if settings else [0, 1, 2, 3, 4, 5]
    now = now or now_utc()
    tz = local_tz()
    today_local = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.scalars(select(DailyTask).where(DailyTask.status.in_(OPEN), DailyTask.due_at.is_not(None),
                                              DailyTask.due_at < today_local)).all()
    target = today_local if today_local.weekday() in working else next_working_day(today_local, working)
    for t in rows:
        if t.status == TaskStatus.BLOCKED:
            continue
        local_due = t.due_at.astimezone(tz)
        t.due_at = target.replace(hour=local_due.hour, minute=local_due.minute)
        t.carry_count += 1
        t.status = TaskStatus.PENDING
        t.notes = ((t.notes + "\n") if t.notes else "") + f"Carried forward from {local_due.date().isoformat()}"
    return len(rows)
