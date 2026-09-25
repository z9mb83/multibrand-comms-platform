import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/comms_test")
os.environ["APP_ENV"] = "test"
os.environ["PROVIDER_MODE"] = "mock"
os.environ["TASK_BACKEND"] = "inline"
os.environ.setdefault("UPLOAD_DIR", "/tmp/comms-test-uploads")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core import db as dbmod  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.models import Base, Brand, BrandMembership, SenderAccount, User  # noqa: E402
from app.providers.mock import OUTBOX  # noqa: E402
from app.services.contacts import set_consent, upsert_brand_contact  # noqa: E402

get_settings.cache_clear()
ENGINE = create_engine(get_settings().database_url, pool_size=30, max_overflow=30)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(ENGINE)
    Base.metadata.create_all(ENGINE)
    dbmod.set_engine(ENGINE)
    yield


@pytest.fixture(autouse=True)
def _clean():
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with ENGINE.begin() as c:
        c.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    OUTBOX.reset()
    yield


@pytest.fixture
def factory() -> sessionmaker:
    return sessionmaker(bind=ENGINE, expire_on_commit=False, autoflush=False)


@pytest.fixture
def db(factory):
    s = factory()
    yield s
    s.close()


def make_senders(db, brand, n=5, channel="email", **kw):
    out = []
    for i in range(1, n + 1):
        addr = f"sender{i}@{brand.slug}.test" if channel == "email" else f"+9190000{brand.id:02d}{i:03d}"
        s = SenderAccount(brand_id=brand.id, channel=channel, address=addr, display_name=f"{brand.name} {i}",
                          provider="mock", status="active", connection_status="connected", rotation_order=i,
                          daily_limit=kw.get("daily_limit", 1000), hourly_limit=kw.get("hourly_limit", 1000),
                          provider_config={}, is_notification_sender=False)
        db.add(s)
        out.append(s)
    db.flush()
    return out


@pytest.fixture
def world(db):
    """Two brands, users with different access, 5 email senders + 1 WhatsApp + 1 SMS per brand."""
    a = Brand(slug="marketing", name="Marketing Co", settings={})
    b = Brand(slug="staffing", name="Staffing Co", settings={})
    db.add_all([a, b])
    db.flush()
    admin = User(email="admin@test.io", full_name="Super Admin", password_hash=hash_password("password123"),
                 is_super_admin=True)
    agent_a = User(email="agent@marketing.io", full_name="Agent A", password_hash=hash_password("password123"))
    db.add_all([admin, agent_a])
    db.flush()
    db.add(BrandMembership(user_id=agent_a.id, brand_id=a.id, role="manager"))
    senders_a = make_senders(db, a, 5)
    senders_b = make_senders(db, b, 5)
    wa_a = make_senders(db, a, 1, channel="whatsapp")[0]
    db.commit()
    return {"a": a, "b": b, "admin": admin, "agent_a": agent_a, "senders_a": senders_a, "senders_b": senders_b,
            "wa_a": wa_a}


def add_contact(db, brand, email=None, phone=None, consent=("email",), name="Test Contact"):
    bc = upsert_brand_contact(db, brand.id, email=email, phone=phone, contact_name=name)
    for ch in consent:
        set_consent(db, brand.id, bc.contact_id, ch, True, source="test")
    db.flush()
    return bc


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def auth(user) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}
