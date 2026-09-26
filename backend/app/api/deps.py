import hmac
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Path, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import decode_access_token
from app.models import Brand, BrandMembership, User
from app.models.enums import Role

bearer = HTTPBearer(auto_error=False)

ROLE_RANK = {Role.VIEWER: 1, Role.AGENT: 2, Role.MANAGER: 3, Role.BRAND_ADMIN: 4}


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    uid = decode_access_token(creds.credentials)
    user = db.get(User, uid) if uid else None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return user


def accessible_brand_ids(db: Session, user: User) -> set[int]:
    memberships = db.execute(select(BrandMembership.brand_id, BrandMembership.role)
                             .where(BrandMembership.user_id == user.id)).all()
    if user.is_super_admin:
        blocked = {b for b, r in memberships if r == Role.BLOCKED}
        return set(db.scalars(select(Brand.id))) - blocked
    return {b for b, r in memberships if r != Role.BLOCKED}


def brand_role(db: Session, user: User, brand_id: int) -> str | None:
    if brand_id not in accessible_brand_ids(db, user):
        return None
    if user.is_super_admin:
        return Role.BRAND_ADMIN
    return db.scalar(select(BrandMembership.role).where(BrandMembership.user_id == user.id,
                                                        BrandMembership.brand_id == brand_id))


@dataclass
class BrandCtx:
    brand_id: int
    user: User
    role: str

    def require(self, min_role: str) -> None:
        if ROLE_RANK.get(self.role, 0) < ROLE_RANK[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {min_role} role for this brand")


def brand_ctx(brand_id: int = Path(...), user: User = Depends(current_user), db: Session = Depends(get_db)) -> BrandCtx:
    role = brand_role(db, user, brand_id)
    if role is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to this brand")
    return BrandCtx(brand_id=brand_id, user=user, role=role)


def require_super_admin(user: User = Depends(current_user)) -> User:
    if not user.is_super_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super Admin only")
    return user


GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
_github_jwks = None


def github_scheduler_token_ok(token: str, jwks_client=None) -> bool:
    """Accept a GitHub Actions OIDC token minted by this repository's default branch."""
    s = get_settings()
    if not s.scheduler_github_repo:
        return False
    global _github_jwks
    try:
        import jwt as pyjwt
        if jwks_client is None:
            if _github_jwks is None:
                _github_jwks = pyjwt.PyJWKClient(f"{GITHUB_OIDC_ISSUER}/.well-known/jwks")
            jwks_client = _github_jwks
        key = jwks_client.get_signing_key_from_jwt(token).key
        claims = pyjwt.decode(token, key, algorithms=["RS256"], audience=s.scheduler_oidc_audience,
                              issuer=GITHUB_OIDC_ISSUER)
    except Exception:  # noqa: BLE001 - any verification problem means "not this caller"
        return False
    return (claims.get("repository", "").lower() == s.scheduler_github_repo.lower()
            and claims.get("ref") == "refs/heads/main")


def internal_auth(x_internal_token: str | None = Header(default=None),
                  authorization: str | None = Header(default=None)) -> None:
    """Local/test: shared token. Production: verify Google OIDC token from Cloud Tasks / Scheduler."""
    s = get_settings()
    from app.core.config import LOCAL_INTERNAL
    if x_internal_token and hmac.compare_digest(x_internal_token, s.internal_token) and (
            s.app_env != "production" or s.internal_token != LOCAL_INTERNAL):
        return
    if authorization and authorization.startswith("Bearer ") and github_scheduler_token_ok(authorization[7:]):
        return
    if authorization and authorization.startswith("Bearer ") and s.internal_oidc_audience:
        try:
            from google.auth.transport import requests as grequests  # type: ignore
            from google.oauth2 import id_token  # type: ignore
            id_token.verify_oauth2_token(authorization[7:], grequests.Request(), audience=s.internal_oidc_audience)
            return
        except Exception:
            pass
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "internal endpoint")
