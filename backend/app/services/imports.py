"""Excel / CSV bulk import: upload -> preview (validate) -> commit. Never sends anything."""
import hashlib
import io
import os
import re
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Brand, Campaign, ImportBatch, ImportRow, OutboundMessage, SenderAccount, Suppression
from app.models.enums import CampaignStatus, Channel, MessageStatus, RoutingMode
from app.services.common import DomainError, audit, local_tz, normalize_email, normalize_phone, now_utc, parse_bool
from app.services.contacts import find_brand_contact, get_consent, set_consent, upsert_brand_contact
from app.services.dispatch import create_message, find_open_conversation

FIELDS = [
    "contact_name", "company_name", "email", "phone", "brand", "channel", "sender_email", "routing_mode",
    "campaign_name", "subject", "message", "template_id", "scheduled_at", "follow_up_date", "follow_up_owner",
    "consent_email", "consent_whatsapp", "consent_sms", "consent_source", "notes",
]

# Default header synonyms; extend per deployment via options["column_aliases"] or brand.settings["column_aliases"].
SYNONYMS: dict[str, list[str]] = {
    "contact_name": ["name", "full name", "contact", "contact person", "customer name", "lead name"],
    "company_name": ["company", "organisation", "organization", "business", "firm"],
    "email": ["email address", "e-mail", "mail", "email id"],
    "phone": ["mobile", "mobile number", "phone number", "contact number", "whatsapp", "whatsapp number", "cell"],
    "brand": ["brand name", "business unit", "workspace"],
    "channel": ["medium", "send via"],
    "sender_email": ["from", "from email", "sender", "send from"],
    "routing_mode": ["routing", "mode"],
    "campaign_name": ["campaign"],
    "subject": ["email subject", "subject line"],
    "message": ["body", "message body", "text", "content"],
    "template_id": ["template"],
    "scheduled_at": ["schedule", "send at", "send time", "scheduled"],
    "follow_up_date": ["follow up", "followup", "follow-up date", "next follow up"],
    "follow_up_owner": ["owner", "assigned to", "follow up owner"],
    "consent_email": ["email consent", "email opt in"],
    "consent_whatsapp": ["whatsapp consent", "wa consent", "whatsapp opt in"],
    "consent_sms": ["sms consent", "sms opt in"],
    "consent_source": ["consent from", "source", "lead source"],
    "notes": ["note", "remarks", "comments"],
}


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_\-]+", " ", str(h).strip().lower())


def detect_mapping(columns: list[str], extra_aliases: dict[str, list[str]] | None = None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for f in FIELDS:
        for alias in [f, *SYNONYMS.get(f, []), *((extra_aliases or {}).get(f, []))]:
            lookup.setdefault(_norm_header(alias), f)
    mapping: dict[str, str] = {}
    for col in columns:
        f = lookup.get(_norm_header(col))
        if f and f not in mapping:
            mapping[f] = col
    return mapping


def read_workbook(data: bytes, filename: str) -> dict[str, pd.DataFrame]:
    ext = os.path.splitext(filename.lower())[1]
    buf = io.BytesIO(data)
    try:
        if ext == ".csv":
            return {"CSV": pd.read_csv(buf, dtype=str, keep_default_na=False)}
        if ext in (".xlsx", ".xlsm"):
            return pd.read_excel(buf, sheet_name=None, dtype=str, keep_default_na=False, engine="openpyxl")
        if ext == ".xls":
            return pd.read_excel(buf, sheet_name=None, dtype=str, keep_default_na=False, engine="xlrd")
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(f"Could not read the file: {exc}") from exc
    raise DomainError("Unsupported file type; upload .xlsx, .xls or .csv")


def upload(db: Session, *, filename: str, data: bytes, user_id: int, default_brand_id: int | None) -> ImportBatch:
    digest = hashlib.sha256(data).hexdigest()
    sheets = read_workbook(data, filename)
    info = []
    for name, df in sheets.items():
        cols = [str(c) for c in df.columns]
        info.append({"name": name, "columns": cols, "rows": int(len(df)), "detected_mapping": detect_mapping(cols),
                     "sample": df.head(5).to_dict(orient="records")})
    prior = db.scalars(select(ImportBatch).where(ImportBatch.file_hash == digest, ImportBatch.status == "committed")
                       .order_by(ImportBatch.id)).first()
    batch = ImportBatch(uploaded_by=user_id, default_brand_id=default_brand_id, filename=filename, file_hash=digest,
                        storage_path="db", file_data=data, sheets=info, duplicate_of_batch_id=prior.id if prior else None,
                        status="uploaded", mapping={}, options={}, counts={}, selected_sheets=[])
    db.add(batch)
    db.flush()
    audit(db, "import.uploaded", user_id=user_id, brand_id=default_brand_id, entity_type="import_batch",
          entity_id=batch.id, filename=filename, duplicate_of=batch.duplicate_of_batch_id)
    return batch


def _cell(row: dict, mapping: dict, field: str):
    col = mapping.get(field)
    if not col:
        return None
    v = row.get(col)
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    ts = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        raise ValueError(f"invalid date '{value}'")
    dt = ts.to_pydatetime()
    return dt.replace(tzinfo=local_tz()) if dt.tzinfo is None else dt


def idempotency_key(brand_id: int, channel: str, recipient: str, campaign: str | None, subject: str | None,
                    message: str | None, template: str | None) -> str:
    base = "|".join(str(x or "") for x in (brand_id, channel, recipient, campaign, subject, message, template))
    return "imp-" + hashlib.sha256(base.encode()).hexdigest()[:48]


def preview(db: Session, batch: ImportBatch, *, sheets: list[str], mapping: dict[str, str], options: dict,
            allowed_brand_ids: set[int]) -> dict:
    """Validate every row and store results in import_rows. Replaces any earlier preview for the batch."""
    if batch.status == "committed":
        raise DomainError("batch already committed", 409)
    unknown = set(mapping) - set(FIELDS)
    if unknown:
        raise DomainError(f"unknown target fields: {sorted(unknown)}")
    brand_mode = options.get("brand_mode", "single")
    single_brand_id = options.get("brand_id") or batch.default_brand_id
    if brand_mode == "single":
        if not single_brand_id:
            raise DomainError("brand_id is required when brand_mode is 'single'")
        if single_brand_id not in allowed_brand_ids:
            raise DomainError("not authorised for this brand", 403)
    elif "brand" not in mapping:
        raise DomainError("map a 'brand' column when brand_mode is 'column'")
    default_channel = options.get("default_channel", Channel.EMAIL)
    default_mode = options.get("default_routing_mode", RoutingMode.AUTO)
    default_campaign = options.get("campaign_name")
    default_schedule = options.get("scheduled_at")

    brands = db.scalars(select(Brand)).all()
    brand_lookup = {}
    for b in brands:
        brand_lookup[b.slug.lower()] = b.id
        brand_lookup[b.name.lower()] = b.id
        brand_lookup[str(b.id)] = b.id
    senders = db.scalars(select(SenderAccount)).all()
    sender_lookup = {(s.brand_id, s.channel, s.address.lower()): s for s in senders}

    db.query(ImportRow).filter(ImportRow.batch_id == batch.id).delete()
    if batch.file_data is None:
        raise DomainError("original file is no longer stored; upload it again", 409)
    wb = read_workbook(batch.file_data, batch.filename)
    seen: set[str] = set()
    counts = {"total": 0, "valid": 0, "invalid": 0, "duplicate": 0, "skipped": 0, "with_message": 0}
    for sheet in sheets:
        if sheet not in wb:
            raise DomainError(f"sheet '{sheet}' not found")
        df = wb[sheet]
        for idx, row in enumerate(df.to_dict(orient="records"), start=2):  # header is row 1
            counts["total"] += 1
            errors: list[str] = []
            notes: list[str] = []
            status = "valid"
            if all(not str(v).strip() for v in row.values()):
                db.add(ImportRow(batch_id=batch.id, sheet_name=sheet, row_number=idx, raw=row, status="skipped",
                                 errors=["empty row"], normalized={}))
                counts["skipped"] += 1
                continue
            g = lambda f, _row=row: _cell(_row, mapping, f)  # noqa: E731

            # brand
            brand_id = None
            if brand_mode == "single":
                brand_id = single_brand_id
            else:
                bval = (g("brand") or "").lower()
                brand_id = brand_lookup.get(bval)
                if not bval:
                    errors.append("brand missing")
                elif brand_id is None:
                    errors.append(f"unknown brand '{g('brand')}'")
                elif brand_id not in allowed_brand_ids:
                    errors.append(f"not authorised for brand '{g('brand')}'")
                    brand_id = None

            channel = (g("channel") or default_channel).lower()
            if channel not in {c.value for c in Channel}:
                errors.append(f"invalid channel '{channel}'")
            email_raw, phone_raw = g("email"), g("phone")
            email = normalize_email(email_raw)
            phone = normalize_phone(phone_raw)
            if email_raw and not email:
                errors.append(f"invalid email '{email_raw}'")
            if phone_raw and not phone:
                errors.append(f"invalid phone '{phone_raw}'")
            if not email and not phone:
                errors.append("email or phone required")
            if channel == "email" and not email:
                errors.append("email channel requires a valid email")
            if channel in ("whatsapp", "sms") and not phone:
                errors.append(f"{channel} channel requires a valid phone")

            message, subject, template = g("message"), g("subject"), g("template_id")
            has_message = bool(message or template)
            campaign_name = g("campaign_name") or default_campaign

            # routing / sender
            sender_email = (g("sender_email") or "").lower() or None
            mode = (g("routing_mode") or ("FIXED" if sender_email else default_mode)).upper()
            requested_sender_id = None
            if mode not in {m.value for m in RoutingMode}:
                errors.append(f"invalid routing_mode '{mode}'")
            if sender_email:
                if channel != "email":
                    errors.append("sender_email is only valid for the email channel")
                elif brand_id is not None:
                    acc = sender_lookup.get((brand_id, "email", sender_email))
                    if acc is None:
                        errors.append(f"sender {sender_email} does not belong to the selected brand")
                    else:
                        requested_sender_id = acc.id
            elif mode == RoutingMode.FIXED:
                errors.append("routing_mode FIXED requires sender_email")

            # consent
            consent_vals = {ch: parse_bool(g(f"consent_{ch}")) for ch in ("email", "whatsapp", "sms")}
            bc = None
            if brand_id is not None and (email or phone):
                bc = find_brand_contact(db, brand_id, email, phone)
            recipient = email if channel == "email" else phone
            if has_message and brand_id is not None and channel in {c.value for c in Channel} and recipient:
                existing_consent = get_consent(db, brand_id, bc.contact_id, channel) if bc else "unknown"
                if consent_vals.get(channel) is False or (consent_vals.get(channel) is None and existing_consent != "granted"):
                    errors.append(f"no {channel} consent for this recipient")
                if db.scalar(select(Suppression.id).where(Suppression.brand_id == brand_id, Suppression.channel == channel,
                                                          Suppression.address == recipient)):
                    errors.append(f"recipient is on the {channel} suppression list")
                if bc is not None and channel == "email":
                    conv = find_open_conversation(db, brand_id, bc.contact_id, "email")
                    if conv and conv.sender_account_id:
                        if requested_sender_id and requested_sender_id != conv.sender_account_id:
                            notes.append("active thread exists: original sender will be preserved")
                        mode = RoutingMode.STICKY
                        requested_sender_id = conv.sender_account_id

            try:
                sched = _parse_dt(g("scheduled_at")) or (_parse_dt(default_schedule) if default_schedule else None)
                fu_date = _parse_dt(g("follow_up_date"))
            except ValueError as exc:
                errors.append(str(exc))
                sched = fu_date = None

            key = None
            if brand_id is not None and recipient:
                if has_message:
                    key = idempotency_key(brand_id, channel, recipient, campaign_name, subject, message, template)
                    dedupe_key = key
                else:
                    dedupe_key = f"contact|{brand_id}|{email or phone}"
                if dedupe_key in seen:
                    status = "duplicate"
                    errors.append("duplicate of an earlier row in this file")
                seen.add(dedupe_key)
                if key and status != "duplicate" and db.scalar(select(OutboundMessage.id).where(
                        OutboundMessage.idempotency_key == key)):
                    status = "duplicate"
                    errors.append("already imported (same brand, recipient, campaign and message)")
            if errors and status != "duplicate":
                status = "invalid"
            normalized = {
                "brand_id": brand_id, "channel": channel, "email": email, "phone": phone,
                "contact_name": g("contact_name"), "company_name": g("company_name"), "subject": subject,
                "message": message, "template_id": template, "campaign_name": campaign_name,
                "routing_mode": mode, "requested_sender_id": requested_sender_id,
                "scheduled_at": sched.isoformat() if sched else None,
                "follow_up_date": fu_date.isoformat() if fu_date else None,
                "follow_up_owner": g("follow_up_owner"), "consent": consent_vals,
                "consent_source": g("consent_source") or f"import:{batch.filename}", "notes": g("notes"),
                "has_message": has_message, "info": notes,
            }
            db.add(ImportRow(batch_id=batch.id, sheet_name=sheet, row_number=idx, brand_id=brand_id, raw=row,
                             normalized=normalized, status=status, errors=errors, idempotency_key=key))
            counts[status] += 1
            if status == "valid" and has_message:
                counts["with_message"] += 1
    batch.selected_sheets, batch.mapping, batch.options, batch.counts = sheets, mapping, options, counts
    batch.status = "previewed"
    db.flush()
    return counts


def commit(db: Session, batch: ImportBatch, user_id: int, allow_duplicate_file: bool = False) -> dict:
    from app.models import DailyTask, User

    if batch.status != "previewed":
        raise DomainError(f"batch must be previewed first (status={batch.status})", 409)
    if batch.duplicate_of_batch_id and not allow_duplicate_file:
        raise DomainError(f"this exact file was already imported as batch #{batch.duplicate_of_batch_id}; "
                          "confirm with allow_duplicate_file=true (duplicate rows are still skipped)", 409)
    rows = db.scalars(select(ImportRow).where(ImportRow.batch_id == batch.id, ImportRow.status == "valid")
                      .order_by(ImportRow.id)).all()
    campaigns: dict[tuple[int, str], Campaign] = {}
    imported = queued = 0
    for r in rows:
        n = r.normalized
        brand_id = n["brand_id"]
        bc = upsert_brand_contact(db, brand_id, email=n["email"], phone=n["phone"], contact_name=n["contact_name"],
                                  company_name=n["company_name"], notes=n["notes"], source_import_batch_id=batch.id)
        for ch, val in n["consent"].items():
            if val is not None:
                set_consent(db, brand_id, bc.contact_id, ch, bool(val), source=n["consent_source"], user_id=user_id)
        r.contact_id = bc.contact_id
        r.status = "imported"
        imported += 1
        if n["follow_up_date"]:
            owner = None
            if n["follow_up_owner"]:
                owner = db.scalars(select(User.id).where(User.email == n["follow_up_owner"].lower())).first()
            db.add(DailyTask(brand_id=brand_id, contact_id=bc.contact_id, company_name=n["company_name"],
                             assigned_user_id=owner, description=f"Follow up with {n['contact_name'] or n['email'] or n['phone']}",
                             due_at=datetime.fromisoformat(n["follow_up_date"]), source="system", created_by=user_id,
                             notes=f"From import #{batch.id}"))
        if not n["has_message"]:
            continue
        cname = n["campaign_name"] or f"Import #{batch.id} ({n['channel']})"
        camp = campaigns.get((brand_id, cname))
        if camp is None:
            camp = db.scalars(select(Campaign).where(Campaign.brand_id == brand_id, Campaign.name == cname)).first()
            if camp is None:
                camp = Campaign(brand_id=brand_id, name=cname, channel=n["channel"], status=CampaignStatus.DRAFT,
                                created_by=user_id, source_import_batch_id=batch.id, allowed_sender_ids=[], segment={})
                db.add(camp)
                db.flush()
            campaigns[(brand_id, cname)] = camp
        if camp.channel != n["channel"]:
            camp.channel = "mixed"
        tpl_id = int(n["template_id"]) if (n["template_id"] or "").isdigit() else None
        msg = create_message(db, brand_id=brand_id, contact_id=bc.contact_id, channel=n["channel"],
                             to_address=n["email"] if n["channel"] == "email" else n["phone"], subject=n["subject"],
                             body=n["message"], template_id=tpl_id, campaign_id=camp.id,
                             routing_mode=n["routing_mode"], requested_sender_id=n["requested_sender_id"],
                             scheduled_at=datetime.fromisoformat(n["scheduled_at"]) if n["scheduled_at"] else None,
                             status=MessageStatus.DRAFT, idempotency_key=r.idempotency_key)
        msg.import_row_ref = f"{batch.id}:{r.sheet_name}:{r.row_number}"
        r.outbound_message_id = msg.id
        r.status = "queued"
        queued += 1
    batch.status, batch.committed_at = "committed", now_utc()
    batch.file_data = None  # keep only the validated rows, not the raw contact file
    batch.counts = {**batch.counts, "imported": imported, "queued_for_approval": queued,
                    "campaign_ids": [c.id for c in campaigns.values()]}
    audit(db, "import.committed", user_id=user_id, entity_type="import_batch", entity_id=batch.id, **batch.counts)
    return batch.counts


def error_workbook(db: Session, batch: ImportBatch) -> bytes:
    rows = db.scalars(select(ImportRow).where(ImportRow.batch_id == batch.id,
                                              ImportRow.status.in_(["invalid", "duplicate", "skipped"]))
                      .order_by(ImportRow.sheet_name, ImportRow.row_number)).all()
    wb = Workbook()
    ws = wb.active
    ws.title = "Errors"
    cols: list[str] = []
    for r in rows:
        for c in r.raw:
            if c not in cols:
                cols.append(c)
    ws.append(["sheet", "row", "status", "errors", *cols])
    for r in rows:
        ws.append([r.sheet_name, r.row_number, r.status, "; ".join(r.errors), *[str(r.raw.get(c, "")) for c in cols]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
