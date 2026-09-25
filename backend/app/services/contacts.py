from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import BrandContact, Contact, ContactConsent, Suppression
from app.models.enums import Channel, ConsentStatus
from app.services.common import audit, now_utc


def find_brand_contact(db: Session, brand_id: int, email: str | None = None, phone: str | None = None) -> BrandContact | None:
    conds = []
    if email:
        conds.append(BrandContact.email == email)
    if phone:
        conds.append(BrandContact.phone == phone)
    if not conds:
        return None
    return db.scalars(select(BrandContact).where(BrandContact.brand_id == brand_id, or_(*conds)).limit(1)).first()


def upsert_brand_contact(db: Session, brand_id: int, *, email: str | None, phone: str | None,
                         contact_name: str | None = None, company_name: str | None = None,
                         notes: str | None = None, source_import_batch_id: int | None = None) -> BrandContact:
    bc = find_brand_contact(db, brand_id, email, phone)
    if bc is None:
        # global identity (not visible to other brands)
        q = select(Contact)
        if email:
            q = q.where(Contact.email == email)
        else:
            q = q.where(Contact.phone == phone)
        contact = db.scalars(q.limit(1)).first()
        if contact is None:
            contact = Contact(email=email, phone=phone)
            db.add(contact)
            db.flush()
        bc = BrandContact(brand_id=brand_id, contact_id=contact.id, email=email, phone=phone,
                          contact_name=contact_name, company_name=company_name, notes=notes,
                          source_import_batch_id=source_import_batch_id, tags=[])
        db.add(bc)
        db.flush()
    else:
        bc.email = bc.email or email
        bc.phone = bc.phone or phone
        bc.contact_name = contact_name or bc.contact_name
        bc.company_name = company_name or bc.company_name
        if notes:
            bc.notes = f"{bc.notes}\n{notes}" if bc.notes and notes not in bc.notes else notes
    return bc


def set_consent(db: Session, brand_id: int, contact_id: int, channel: str, granted: bool,
                source: str | None = None, user_id: int | None = None) -> ContactConsent:
    c = db.scalars(select(ContactConsent).where(ContactConsent.brand_id == brand_id,
                                                ContactConsent.contact_id == contact_id,
                                                ContactConsent.channel == channel)).first()
    if c is None:
        c = ContactConsent(brand_id=brand_id, contact_id=contact_id, channel=channel)
        db.add(c)
    now = now_utc()
    if granted:
        c.status, c.captured_at, c.withdrawn_at = ConsentStatus.GRANTED, now, None
    else:
        c.status, c.withdrawn_at = ConsentStatus.WITHDRAWN, now
    c.source = source or c.source
    audit(db, "consent.updated", user_id=user_id, brand_id=brand_id, entity_type="contact",
          entity_id=contact_id, channel=channel, granted=granted, source=source)
    return c


def get_consent(db: Session, brand_id: int, contact_id: int, channel: str) -> str:
    c = db.scalars(select(ContactConsent.status).where(ContactConsent.brand_id == brand_id,
                                                       ContactConsent.contact_id == contact_id,
                                                       ContactConsent.channel == channel)).first()
    return c or ConsentStatus.UNKNOWN


def is_suppressed(db: Session, brand_id: int, channel: str, address: str) -> bool:
    return db.scalar(select(Suppression.id).where(Suppression.brand_id == brand_id, Suppression.channel == channel,
                                                  Suppression.address == address)) is not None


def check_can_send(db: Session, brand_id: int, contact_id: int, channel: str, address: str) -> str | None:
    """Return None if sending is allowed, otherwise a reason string."""
    if is_suppressed(db, brand_id, channel, address):
        return "suppressed"
    status = get_consent(db, brand_id, contact_id, channel)
    if status != ConsentStatus.GRANTED:
        return f"no_{channel}_consent" if status == ConsentStatus.UNKNOWN else f"{channel}_consent_withdrawn"
    return None


def unsubscribe(db: Session, brand_id: int, contact_id: int, channel: str | None, address: str | None,
                reason: str = "unsubscribe", user_id: int | None = None) -> None:
    """Unsubscribe within ONE brand. channel=None unsubscribes from all channels of that brand."""
    from app.services.followups import cancel_for_contact  # local import avoids cycle

    bc = db.scalars(select(BrandContact).where(BrandContact.brand_id == brand_id,
                                               BrandContact.contact_id == contact_id)).first()
    channels = [channel] if channel else [c.value for c in Channel]
    for ch in channels:
        addr = address if channel else ((bc.email if ch == Channel.EMAIL else bc.phone) if bc else None)
        if addr and not is_suppressed(db, brand_id, ch, addr):
            db.add(Suppression(brand_id=brand_id, channel=ch, address=addr, reason=reason))
        set_consent(db, brand_id, contact_id, ch, False, source=reason, user_id=user_id)
    cancel_for_contact(db, brand_id, contact_id, reason="unsubscribed")
    audit(db, "contact.unsubscribed", user_id=user_id, brand_id=brand_id, entity_type="contact",
          entity_id=contact_id, channel=channel or "all")
