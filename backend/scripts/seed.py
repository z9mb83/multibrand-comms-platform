"""Seed local development data: 5 brands, a Super Admin, 10 email accounts per brand (5 active),
1 WhatsApp number and 1 SMS header per brand, all using the MOCK provider.

Usage:  python -m scripts.seed          (from backend/, after `alembic upgrade head`)
Login:  admin@example.com / ChangeMe123!   (local only)
"""
from sqlalchemy import select

from app.core.db import session_factory
from app.core.security import hash_password
from app.models import Brand, BrandMembership, SenderAccount, User
from app.services.reports import get_settings_row

BRANDS = [("marketing", "99 Marketing Services", "#2563eb"), ("staffing", "99 Staffing Services", "#16a34a"),
          ("shaadibells", "Shaadi Bells", "#db2777"), ("property", "Property Experts Group", "#ca8a04"),
          ("skyline", "Skyline Capital", "#7c3aed")]


def main() -> None:
    with session_factory()() as db, db.begin():
        admin = db.scalars(select(User).where(User.email == "admin@example.com")).first()
        if admin is None:
            admin = User(email="admin@example.com", full_name="Super Admin", password_hash=hash_password("ChangeMe123!"),
                         is_super_admin=True)
            db.add(admin)
            db.flush()
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
                    db.add(SenderAccount(brand_id=b.id, channel="email", address=addr, display_name=f"{name}",
                                         provider="mock", status="active" if n <= 5 else "inactive",
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
        s = get_settings_row(db)
        s.master_email = s.master_email or "owner@example.com"
    print("Seeded. Login: admin@example.com / ChangeMe123!")


if __name__ == "__main__":
    main()
