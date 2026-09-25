"""Endpoints called by Cloud Tasks / Cloud Scheduler (OIDC in production, shared token locally)."""
from fastapi import APIRouter, Depends

from app.api.deps import internal_auth
from app.core.db import session_factory
from app.services import followups, reports, tasks
from app.services.dispatch import due_message_ids, process_outbound

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(internal_auth)])


@router.post("/dispatch/{message_id}")
def dispatch_one(message_id: int):
    return {"outcome": process_outbound(session_factory(), message_id)}


@router.post("/dispatch-sweep")
def dispatch_sweep(limit: int = 200):
    with session_factory()() as db:
        ids = due_message_ids(db, limit)
    outcomes: dict[str, int] = {}
    for mid in ids:
        o = process_outbound(session_factory(), mid)
        outcomes[o] = outcomes.get(o, 0) + 1
    return {"processed": len(ids), "outcomes": outcomes}


@router.post("/follow-ups/sweep")
def followup_sweep():
    with session_factory()() as db, db.begin():
        return followups.run_sweep(db)


@router.post("/tasks/maintenance")
def task_maintenance():
    with session_factory()() as db, db.begin():
        return {"carried_forward": tasks.carry_forward(db), "marked_overdue": tasks.mark_overdue(db)}


@router.post("/reports/run")
def run_reports():
    with session_factory()() as db, db.begin():
        made = reports.run_due(db)
        delivered = reports.deliver_pending(db)
        return {"generated": [f"{r.kind}:{r.report_date}" for r in made], "delivery": delivered}
