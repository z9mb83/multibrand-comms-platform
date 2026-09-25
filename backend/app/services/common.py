import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuditLog

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class DomainError(Exception):
    """Business rule violation -> HTTP 400/409 at the API layer."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().default_timezone)


def normalize_email(value) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v or v == "nan":
        return None
    return v if EMAIL_RE.match(v) else None


def normalize_phone(value, default_cc: str | None = None) -> str | None:
    """Return E.164 (+91XXXXXXXXXX) or None when invalid."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or raw.lower() == "nan":
        return None
    if raw.endswith(".0"):  # Excel numeric cells
        raw = raw[:-2]
    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if has_plus:
        result = "+" + digits
    elif raw.startswith("00"):
        result = "+" + digits[2:]
    else:
        cc = (default_cc or get_settings().default_country_code).lstrip("+")
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 10:
            result = f"+{cc}{digits}"
        elif len(digits) == 12 and digits.startswith(cc):
            result = "+" + digits
        else:
            return None
    return result if 8 <= len(result) - 1 <= 15 else None


TRUTHY = {"yes", "y", "true", "1", "granted", "opt-in", "optin", "opted in", "consented"}
FALSY = {"no", "n", "false", "0", "withdrawn", "opt-out", "optout", "denied"}


def parse_bool(value) -> bool | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in TRUTHY:
        return True
    if v in FALSY:
        return False
    return None


def audit(db: Session, action: str, *, user_id: int | None = None, brand_id: int | None = None,
          entity_type: str | None = None, entity_id=None, **details) -> None:
    db.add(AuditLog(action=action, user_id=user_id, brand_id=brand_id, entity_type=entity_type,
                    entity_id=str(entity_id) if entity_id is not None else None, details=details))
