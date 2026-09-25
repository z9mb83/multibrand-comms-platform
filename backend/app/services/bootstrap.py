"""First-run setup: create the Super Admin (chosen by the operator) and optionally the brand workspaces."""
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import Brand, BrandMembership, SenderAccount, User
from app.services.common import DomainError, audit
from app.services.reports import get_settings_row

BRANDS = [("marketing", "99 Marketing Services", "#2563eb"), ("staffing", "99 Staffing Services", "#16a34a"),
          ("shaadibells", "Shaadi Bells", "#db2777"), ("property", "Property Experts Group", "#ca8a04"),
          ("skyline", "Skyline Capital", "#7c3aed")]


def needs_setup(db: Session) -> bool:
    return (db.scalar(select(func.count()).select_from(User)) or 0) == 0


def seed_brands(db: Session, admin: User) -> None:
    """Creates the brand workspaces with MOCK senders (10 email IDs, 5 active; 1 WhatsApp; 1 SMS per brand).
    Mock senders never deliver real messages; replace them with real accounts when connecting providers."""
    for i, (slug, name, color) in enumerate(BRANDS, start=1):
        b = db.scalars(select(Brand).where(Brand.slug == slug)).first()
        if b is None:
            b = Brand(slug=slug, name=name, primary_color=color, settings={})
            db.add(b)
            db.flush()
            db.add(BrandMembership(user_id=admin.id, brand_id=b.id, role="brand_admin"))
        for n in range(1, 11):
            addr = f"outreach{n}@{slug}.example.com"
            if not db.scalar(select(SenderAccount.id).where(SenderAccount.brand_id == b.id, SenderAccount.address == addr)):
                db.add(SenderAccount(brand_id=b.id, channel="email", address=addr, display_name=name, provider="mock",
                                     status="active" if n <= 5 else "inactive",
                                     connection_status="connected" if n <= 5 else "pending",
                                     rotation_order=n, daily_limit=200, hourly_limit=40, provider_config={}))
        wa = f"+9190000000{i:02d}"
        if not db.scalar(select(SenderAccount.id).where(SenderAccount.brand_id == b.id, SenderAccount.address == wa)):
            db.add(SenderAccount(brand_id=b.id, channel="whatsapp", address=wa, provider="mock", status="active",
                                 connection_status="connected", daily_limit=1000, hourly_limit=250,
                                 provider_config={"phone_number_id": f"MOCK-PNID-{i}"}))
        hdr = f"{slug[:5].upper():X<6}"[:6]
        if not db.scalar(select(SenderAccount.id).where(SenderAccount.brand_id == b.id, SenderAccount.address == hdr)):
            db.add(SenderAccount(brand_id=b.id, channel="sms", address=hdr, provider="mock", status="active",
                                 connection_status="connected", daily_limit=1000, hourly_limit=250,
                                 provider_config={"header": hdr, "dlt_entity_id": "MOCK-ENTITY"}))


def create_first_admin(db: Session, *, email: str, full_name: str, password: str, with_brands: bool) -> User:
    # Serialise concurrent first-run requests; only the very first one can succeed.
    db.execute(text("SELECT pg_advisory_xact_lock(4242001)"))
    if not needs_setup(db):
        raise DomainError("setup has already been completed", 409)
    if len(password) < 10:
        raise DomainError("password must be at least 10 characters")
    admin = User(email=email.lower(), full_name=full_name, password_hash=hash_password(password), is_super_admin=True)
    db.add(admin)
    db.flush()
    s = get_settings_row(db)
    s.master_email = s.master_email or admin.email
    if with_brands:
        seed_brands(db, admin)
    audit(db, "setup.completed", user_id=admin.id, with_brands=with_brands)
    return admin
