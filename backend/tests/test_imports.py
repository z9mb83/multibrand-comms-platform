import io

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from app.models import BrandContact, Campaign, ImportRow, OutboundMessage
from app.providers.mock import OUTBOX
from app.services import imports
from app.services.common import DomainError
from tests.conftest import add_contact

HEADERS = ["Name", "Company", "Email", "Mobile", "Brand", "Channel", "Sender Email", "Routing Mode", "Campaign",
           "Subject", "Message", "Email Consent", "WhatsApp Consent", "Notes"]


def workbook(sheets: dict[str, list[list]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(HEADERS)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run(db, world, data, options, filename="leads.xlsx", sheets=None, allowed=None):
    batch = imports.upload(db, filename=filename, data=data, user_id=world["admin"].id, default_brand_id=None)
    mapping = batch.sheets[0]["detected_mapping"]
    counts = imports.preview(db, batch, sheets=sheets or [s["name"] for s in batch.sheets], mapping=mapping,
                             options=options, allowed_brand_ids=allowed or {world["a"].id, world["b"].id})
    db.commit()
    return batch, counts


def rows_by_status(db, batch, status):
    return db.scalars(select(ImportRow).where(ImportRow.batch_id == batch.id, ImportRow.status == status)).all()


def test_column_detection_uses_synonyms():
    m = imports.detect_mapping(HEADERS)
    assert m["contact_name"] == "Name" and m["phone"] == "Mobile" and m["sender_email"] == "Sender Email"
    assert m["consent_email"] == "Email Consent" and m["campaign_name"] == "Campaign"
    custom = imports.detect_mapping(["Client Mail"], {"email": ["client mail"]})
    assert custom == {"email": "Client Mail"}


def test_multi_sheet_brand_column_mapping(db, world):
    data = workbook({
        "North": [["Asha", "A Ltd", "asha@a.test", "9876543210", "marketing", "email", "", "", "Launch", "Hi", "Hello", "yes", "", ""],
                  ["Ravi", "R Ltd", "ravi@r.test", "", "Staffing Co", "email", "", "", "Launch", "Hi", "Hello", "yes", "", ""]],
        "South": [["Mina", "M Ltd", "mina@m.test", "", "unknown-brand", "email", "", "", "Launch", "Hi", "Hello", "yes", "", ""],
                  ["Wa", "W Ltd", "", "98765 43211", "marketing", "whatsapp", "", "", "WA", "", "Hey", "", "yes", ""]],
    })
    batch, counts = run(db, world, data, {"brand_mode": "column"})
    assert counts["total"] == 4 and counts["valid"] == 3 and counts["invalid"] == 1
    valid = {r.normalized["email"] or r.normalized["phone"]: r.brand_id for r in rows_by_status(db, batch, "valid")}
    assert valid == {"asha@a.test": world["a"].id, "ravi@r.test": world["b"].id, "+919876543211": world["a"].id}
    bad = rows_by_status(db, batch, "invalid")[0]
    assert bad.sheet_name == "South" and "unknown brand" in bad.errors[0]


def test_brand_column_respects_authorisation(db, world):
    data = workbook({"S": [["Ravi", "", "ravi@r.test", "", "staffing", "email", "", "", "C", "Hi", "Hello", "yes", "", ""]]})
    batch, counts = run(db, world, data, {"brand_mode": "column"}, allowed={world["a"].id})
    assert counts["invalid"] == 1
    assert "not authorised" in rows_by_status(db, batch, "invalid")[0].errors[0]


def test_single_brand_mode_and_sender_validation(db, world):
    own = world["senders_a"][2].address
    foreign = world["senders_b"][0].address
    data = workbook({"S": [
        ["A", "", "a1@x.test", "", "", "email", own, "", "C", "S", "M", "yes", "", ""],
        ["B", "", "b1@x.test", "", "", "email", foreign, "", "C", "S", "M", "yes", "", ""],
        ["C", "", "c1@x.test", "", "", "email", "", "FIXED", "C", "S", "M", "yes", "", ""],
        ["D", "", "d1@x.test", "", "", "email", "", "", "C", "S", "M", "yes", "", ""],
    ]})
    batch, counts = run(db, world, data, {"brand_mode": "single", "brand_id": world["a"].id})
    valid = {r.normalized["email"]: r.normalized for r in rows_by_status(db, batch, "valid")}
    assert valid["a1@x.test"]["routing_mode"] == "FIXED"
    assert valid["a1@x.test"]["requested_sender_id"] == world["senders_a"][2].id
    assert valid["d1@x.test"]["routing_mode"] == "AUTO" and valid["d1@x.test"]["requested_sender_id"] is None
    errs = {r.normalized["email"]: r.errors for r in rows_by_status(db, batch, "invalid")}
    assert "does not belong to the selected brand" in errs["b1@x.test"][0]
    assert "FIXED requires sender_email" in errs["c1@x.test"][0]


def test_import_never_sends_and_creates_draft_campaign(db, world):
    data = workbook({"S": [["A", "", "a@x.test", "", "", "email", "", "", "Autumn", "S", "M", "yes", "", ""]]})
    batch, _ = run(db, world, data, {"brand_mode": "single", "brand_id": world["a"].id})
    result = imports.commit(db, batch, world["admin"].id)
    db.commit()
    assert result["imported"] == 1 and result["queued_for_approval"] == 1
    msg = db.scalars(select(OutboundMessage)).one()
    camp = db.get(Campaign, msg.campaign_id)
    assert msg.status == "draft" and camp.status == "draft" and camp.name == "Autumn"
    assert OUTBOX.sent == []


def test_duplicates_within_file_and_reimport_protection(db, world):
    row = ["A", "", "dup@x.test", "", "", "email", "", "", "C1", "S", "M", "yes", "", ""]
    data = workbook({"S": [row, list(row), ["B", "", "other@x.test", "", "", "email", "", "", "C1", "S", "M", "yes", "", ""]]})
    opts = {"brand_mode": "single", "brand_id": world["a"].id}
    batch, counts = run(db, world, data, opts)
    assert counts["valid"] == 2 and counts["duplicate"] == 1
    imports.commit(db, batch, world["admin"].id)
    db.commit()

    # identical file again: flagged as duplicate file and refused without explicit confirmation
    batch2, counts2 = run(db, world, data, opts)
    assert batch2.duplicate_of_batch_id == batch.id
    assert counts2["valid"] == 0 and counts2["duplicate"] == 3
    with pytest.raises(DomainError):
        imports.commit(db, batch2, world["admin"].id)
    res = imports.commit(db, batch2, world["admin"].id, allow_duplicate_file=True)
    db.commit()
    assert res["queued_for_approval"] == 0
    assert db.query(OutboundMessage).count() == 2
    assert db.query(BrandContact).count() == 2


def test_import_consent_validation(db, world):
    data = workbook({"S": [
        ["A", "", "noconsent@x.test", "", "", "email", "", "", "C", "S", "M", "", "", ""],
        ["B", "", "declined@x.test", "", "", "email", "", "", "C", "S", "M", "no", "", ""],
        ["C", "", "contactonly@x.test", "", "", "email", "", "", "", "", "", "", "", "just a contact"],
    ]})
    batch, counts = run(db, world, data, {"brand_mode": "single", "brand_id": world["a"].id})
    errs = {r.normalized["email"]: r.errors for r in rows_by_status(db, batch, "invalid")}
    assert "no email consent" in errs["noconsent@x.test"][0]
    assert "no email consent" in errs["declined@x.test"][0]
    assert counts["valid"] == 1  # contact-only rows need no consent


def test_existing_consent_in_db_is_accepted(db, world):
    add_contact(db, world["a"], email="known@x.test", consent=("email",))
    db.commit()
    data = workbook({"S": [["K", "", "known@x.test", "", "", "email", "", "", "C", "S", "M", "", "", ""]]})
    _, counts = run(db, world, data, {"brand_mode": "single", "brand_id": world["a"].id})
    assert counts["valid"] == 1


def test_error_workbook_and_csv(db, world):
    csv = ("name,email,brand,message,email consent\n"
           "Ok,ok@x.test,marketing,Hello,yes\n"
           "Bad,not-an-email,marketing,Hello,yes\n").encode()
    batch, counts = run(db, world, csv, {"brand_mode": "column"}, filename="leads.csv")
    assert counts["valid"] == 1 and counts["invalid"] == 1
    wb = load_workbook(io.BytesIO(imports.error_workbook(db, batch)))
    rows = list(wb["Errors"].values)
    assert rows[0][:4] == ("sheet", "row", "status", "errors")
    assert rows[1][1] == 3 and "invalid email" in rows[1][3]
