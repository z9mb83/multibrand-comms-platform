"""Bring data over from the Google Sheet (File > Download > .csv or .xlsx of a tab).

kinds:
  leads        lead tab: Person Name, Email, Details (Optional), Subject (Auto), Generated Email (Auto), Send Status,
               Sent At, Error, Sent From, Approval, Gmail Message ID, Follow-up Status (extra/missing columns are fine)
  send_log     Send Log tab: Date and Time, Gmail Sender, Lead Email, Subject, Gmail Message ID, Sending Result, ...
               Importing it matters: the "already contacted" check uses this history.
  suppression  Suppression tab: first column is the email address
The header row is found automatically (the lead tab's headers are on row 2 in the Sheet).
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OutreachLead, OutreachSendLog
from app.models.outreach import OutreachSuppression
from app.services.common import DomainError
from app.services.outreach.engine import STATUS_VALUES, is_valid_email, normalize_email_address

LEAD_FIELDS = {
    "person_name": ["person name", "name", "full name", "contact name"],
    "email": ["email", "email address", "e-mail", "lead email"],
    "details": ["details (optional)", "details", "notes"],
    "subject": ["subject (auto)", "subject"],
    "body": ["generated email (auto)", "generated email", "body", "email body"],
    "status": ["send status", "status"],
    "sent_at": ["sent at"],
    "error": ["error"],
    "sent_from": ["sent from"],
    "approval": ["approval", "approved"],
    "gmail_message_id": ["gmail message id", "message id"],
    "follow_up_status": ["follow-up status", "follow up status"],
}
LOG_FIELDS = {
    "logged_at": ["date and time", "date", "time", "sent at"],
    "sender_email": ["gmail sender", "sender", "sent from"],
    "lead_email": ["lead email", "email", "recipient"],
    "subject": ["subject"],
    "gmail_message_id": ["gmail message id", "message id"],
    "result": ["sending result", "result", "status"],
    "error": ["error reason", "error"],
    "daily_limit": ["daily limit"],
    "stage": ["warm-up stage", "stage"],
}
SUPPRESSION_FIELDS = {"email": ["email", "email address", "suppressed email"], "reason": ["reason", "notes"]}


def _norm(h) -> str:
    return re.sub(r"\s+", " ", str(h or "").strip().lower())


def read_rows(data: bytes, filename: str) -> list[list[str]]:
    ext = os.path.splitext(filename.lower())[1]
    buf = io.BytesIO(data)
    try:
        if ext == ".csv":
            import csv
            text = data.decode("utf-8-sig", errors="replace")
            return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text))]
        if ext in (".xlsx", ".xlsm"):
            df = pd.read_excel(buf, dtype=str, keep_default_na=False, header=None, engine="openpyxl")
        else:
            raise DomainError("Upload a .csv or .xlsx file (Google Sheets: File > Download)")
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(f"Could not read the file: {exc}") from exc
    return [[str(v).strip() for v in row] for row in df.fillna("").values.tolist()]


def _map(rows: list[list[str]], fields: dict[str, list[str]], required: str) -> tuple[int, dict[str, int]]:
    for i, row in enumerate(rows[:10]):
        normed = [_norm(c) for c in row]
        mapping: dict[str, int] = {}
        for field_name, aliases in fields.items():
            for alias in aliases:
                if alias in normed and normed.index(alias) not in mapping.values():
                    mapping[field_name] = normed.index(alias)
                    break
        if required in mapping:
            return i, mapping
    if required == "email" and rows:  # headerless single-column suppression list
        return -1, {"email": 0}
    raise DomainError("Could not find the header row (it needs an Email column)")


def _cell(row: list[str], m: dict[str, int], f: str) -> str:
    return row[m[f]].strip() if f in m and m[f] < len(row) else ""


def _dt(value: str, tz: ZoneInfo) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed


def import_file(db: Session, brand_id: int, kind: str, data: bytes, filename: str, user_id: int | None,
                tz: ZoneInfo = ZoneInfo("Asia/Kolkata")) -> dict:
    rows = read_rows(data, filename)
    added = skipped = 0
    problems: list[str] = []
    if kind == "leads":
        header, m = _map(rows, LEAD_FIELDS, "email")
        existing = {e for (e,) in db.execute(select(OutreachLead.email).where(OutreachLead.brand_id == brand_id))}
        for row in rows[header + 1:]:
            def get(f, row=row):
                return _cell(row, m, f)
            name, raw = get("person_name"), get("email")
            if not name and not raw:
                continue
            email, _ = normalize_email_address(raw)
            if email in existing:
                skipped += 1
                continue
            status = get("status") if get("status") in STATUS_VALUES else ("Generated" if get("subject") and get("body") else "Pending")
            db.add(OutreachLead(brand_id=brand_id, person_name=name, email=email, details=get("details"),
                                subject=get("subject"), body=get("body"), status=status, sent_at=_dt(get("sent_at"), tz),
                                error=get("error"), sent_from=get("sent_from").lower(),
                                approval="Yes" if get("approval").lower() == "yes" else get("approval")[:10],
                                gmail_message_id=get("gmail_message_id"), gmail_thread_id="",
                                follow_up_status=get("follow_up_status"), created_by=user_id))
            existing.add(email)
            added += 1
    elif kind == "send_log":
        header, m = _map(rows, LOG_FIELDS, "lead_email")
        seen = {(s.lower(), e.lower(), t, r) for s, e, t, r in db.execute(
            select(OutreachSendLog.sender_email, OutreachSendLog.lead_email, OutreachSendLog.logged_at, OutreachSendLog.result)
            .where(OutreachSendLog.brand_id == brand_id))}
        for n, row in enumerate(rows[header + 1:], start=header + 2):
            def get(f, row=row):
                return _cell(row, m, f)
            email = get("lead_email").lower()
            if not email:
                continue
            when = _dt(get("logged_at"), tz)
            if when is None:
                problems.append(f"row {n}: unreadable date")
                skipped += 1
                continue
            result = get("result") or "Sent"
            key = (get("sender_email").lower(), email, when, result)
            if key in seen:
                skipped += 1
                continue
            try:
                limit = int(float(get("daily_limit"))) if get("daily_limit") else None
            except ValueError:
                limit = None
            db.add(OutreachSendLog(brand_id=brand_id, logged_at=when, sender_email=key[0], lead_email=email,
                                   subject=get("subject"), gmail_message_id=get("gmail_message_id"), gmail_thread_id="",
                                   result=result, error=get("error"), daily_limit=limit, stage=get("stage"), imported=True))
            seen.add(key)
            added += 1
    elif kind == "suppression":
        header, m = _map(rows, SUPPRESSION_FIELDS, "email")
        existing = {e for (e,) in db.execute(select(OutreachSuppression.email).where(OutreachSuppression.brand_id == brand_id))}
        for row in rows[header + 1:]:
            email = (row[m["email"]] if m["email"] < len(row) else "").strip().lower()
            if not is_valid_email(email) or email in existing:
                skipped += 1 if email else 0
                continue
            reason = row[m["reason"]].strip() if "reason" in m and m["reason"] < len(row) else ""
            db.add(OutreachSuppression(brand_id=brand_id, email=email, reason=reason[:255]))
            existing.add(email)
            added += 1
    else:
        raise DomainError("kind must be leads, send_log or suppression")
    db.commit()
    return {"added": added, "skipped": skipped, "problems": problems[:20]}
