"""Seed LOCAL development data: a Super Admin with a known password plus the brand workspaces (mock senders).

Usage:  python -m scripts.seed          (from backend/, after `alembic upgrade head`)
Login:  admin@example.com / ChangeMe123!   (local only — hosted deployments use the first-run setup screen)
"""
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import session_factory
from app.core.security import hash_password
from app.models import User
from app.services.bootstrap import seed_brands
from app.services.reports import get_settings_row


def main() -> None:
    if get_settings().app_env == "production":
        raise SystemExit("Refusing to seed a known password in production; use the first-run setup screen.")
    with session_factory()() as db, db.begin():
        admin = db.scalars(select(User).where(User.email == "admin@example.com")).first()
        if admin is None:
            admin = User(email="admin@example.com", full_name="Super Admin", password_hash=hash_password("ChangeMe123!"),
                         is_super_admin=True)
            db.add(admin)
            db.flush()
        seed_brands(db, admin)
        s = get_settings_row(db)
        s.master_email = s.master_email or "owner@example.com"
    print("Seeded. Login: admin@example.com / ChangeMe123!")


if __name__ == "__main__":
    main()
