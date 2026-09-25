from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import accessible_brand_ids, current_user
from app.core.db import get_db
from app.models import ImportBatch, ImportRow, User
from app.services import imports as svc

router = APIRouter(prefix="/imports", tags=["imports"])
MAX_BYTES = 20 * 1024 * 1024


class PreviewIn(BaseModel):
    sheets: list[str]
    mapping: dict[str, str]
    options: dict = {}


class CommitIn(BaseModel):
    allow_duplicate_file: bool = False


def _batch(db: Session, batch_id: int, user: User) -> ImportBatch:
    b = db.get(ImportBatch, batch_id)
    if b is None or (b.uploaded_by != user.id and not user.is_super_admin):
        raise HTTPException(404, "import not found")
    return b


def _out(b: ImportBatch) -> dict:
    return {"id": b.id, "filename": b.filename, "status": b.status, "sheets": b.sheets, "counts": b.counts,
            "mapping": b.mapping, "options": b.options, "duplicate_of_batch_id": b.duplicate_of_batch_id,
            "created_at": b.created_at, "committed_at": b.committed_at}


@router.get("/fields")
def fields():
    return {"fields": svc.FIELDS, "synonyms": svc.SYNONYMS}


@router.get("")
def list_batches(user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(ImportBatch).order_by(ImportBatch.id.desc()).limit(50)
    if not user.is_super_admin:
        q = q.where(ImportBatch.uploaded_by == user.id)
    return [_out(b) for b in db.scalars(q)]


@router.post("/upload")
async def upload(file: UploadFile = File(...), brand_id: int | None = Form(None),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (20 MB max)")
    if brand_id is not None and brand_id not in accessible_brand_ids(db, user):
        raise HTTPException(403, "No access to this brand")
    b = svc.upload(db, filename=file.filename or "upload.xlsx", data=data, user_id=user.id, default_brand_id=brand_id)
    db.commit()
    return _out(b)


@router.post("/{batch_id}/preview")
def preview(batch_id: int, body: PreviewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = _batch(db, batch_id, user)
    counts = svc.preview(db, b, sheets=body.sheets, mapping=body.mapping, options=body.options,
                         allowed_brand_ids=accessible_brand_ids(db, user))
    db.commit()
    return {"batch": _out(b), "counts": counts}


@router.get("/{batch_id}/rows")
def rows(batch_id: int, status: str | None = None, limit: int = 200, offset: int = 0,
         user: User = Depends(current_user), db: Session = Depends(get_db)):
    _batch(db, batch_id, user)
    q = select(ImportRow).where(ImportRow.batch_id == batch_id)
    if status:
        q = q.where(ImportRow.status == status)
    items = db.scalars(q.order_by(ImportRow.sheet_name, ImportRow.row_number).limit(limit).offset(offset)).all()
    return [{"id": r.id, "sheet": r.sheet_name, "row": r.row_number, "status": r.status, "errors": r.errors,
             "brand_id": r.brand_id, "normalized": r.normalized} for r in items]


@router.post("/{batch_id}/commit")
def commit(batch_id: int, body: CommitIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = _batch(db, batch_id, user)
    counts = svc.commit(db, b, user.id, body.allow_duplicate_file)
    db.commit()
    return {"batch": _out(b), "counts": counts,
            "note": "Messages were created as drafts inside draft campaigns. Nothing is sent until a campaign is approved and launched."}


@router.get("/{batch_id}/errors.xlsx")
def error_report(batch_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = _batch(db, batch_id, user)
    return Response(svc.error_workbook(db, b),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="import-{b.id}-errors.xlsx"'})
