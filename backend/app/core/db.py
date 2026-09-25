from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        # prepare_threshold=None keeps psycopg compatible with transaction-pooling proxies (Neon, PgBouncer)
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=3, max_overflow=5,
                                pool_recycle=300, connect_args={"prepare_threshold": None})
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def set_engine(engine) -> None:
    """Used by tests to point the app at a dedicated database."""
    global _engine, _SessionLocal
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def session_factory() -> sessionmaker:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()
