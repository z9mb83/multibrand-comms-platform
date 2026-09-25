import hashlib
import hmac
import os
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_JWT = "local-dev-only-secret-change-me-0123456789"
LOCAL_INTERNAL = "local-internal-token"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_env: str = "local"  # local | test | staging | production
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/comms"

    jwt_secret: str = LOCAL_JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12

    # Providers: "mock" for local/test, "live" to use real adapters
    provider_mode: str = "mock"

    # Background execution: "inline" runs tasks in-process, "cloud_tasks" enqueues
    task_backend: str = "inline"
    gcp_project: str = ""
    gcp_region: str = "asia-south1"
    cloud_tasks_queue: str = "outbound"
    internal_base_url: str = "http://localhost:8000"
    internal_token: str = LOCAL_INTERNAL
    internal_oidc_audience: str = ""

    upload_dir: str = "./.uploads"
    default_country_code: str = "+91"
    default_timezone: str = "Asia/Kolkata"

    cors_origins: str = "http://localhost:3000"

    whatsapp_app_secret_ref: str = ""
    whatsapp_verify_token: str = "local-verify-token"

    sending_lease_seconds: int = 300

    @model_validator(mode="after")
    def _platform_defaults(self):
        # Empty or template placeholder values (e.g. auto-imported from .env.example) count as unset.
        def unset(v: str) -> bool:
            v = (v or "").strip().lower()
            return not v or v.startswith("replace") or v.startswith("change-me") or v == "replace-me"

        if unset(self.app_env):
            self.app_env = "local"
        if unset(self.provider_mode) or self.provider_mode not in ("mock", "live"):
            self.provider_mode = "mock"
        if unset(self.task_backend) or self.task_backend not in ("inline", "cloud_tasks"):
            self.task_backend = "inline"
        if unset(self.jwt_secret):
            self.jwt_secret = LOCAL_JWT
        if unset(self.internal_token):
            self.internal_token = LOCAL_INTERNAL
        if unset(self.default_timezone):
            self.default_timezone = "Asia/Kolkata"
        if unset(self.default_country_code):
            self.default_country_code = "+91"
        # Managed Postgres providers (Neon/Vercel/Heroku) hand out postgres:// URLs; we use psycopg 3.
        url = self.database_url
        if "localhost" in url and os.environ.get("VERCEL") == "1" and os.environ.get("POSTGRES_URL"):
            url = os.environ["POSTGRES_URL"]  # a copied local placeholder must never win on Vercel
        if "DATABASE_URL" not in os.environ and os.environ.get("POSTGRES_URL"):
            url = os.environ["POSTGRES_URL"]  # name used by some Vercel storage integrations
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+psycopg://" + url[len(prefix):]
        self.database_url = url
        if os.environ.get("VERCEL") == "1":
            if self.app_env == "local":
                self.app_env = "production"
            self.upload_dir = "/tmp/uploads"
            # Derive per-deployment secrets from the injected database credential so no secret has to be
            # typed into a dashboard. Explicit JWT_SECRET / INTERNAL_TOKEN env vars still take precedence.
            if self.jwt_secret == LOCAL_JWT:
                self.jwt_secret = hmac.new(url.encode(), b"jwt-secret", hashlib.sha256).hexdigest()
            if self.internal_token == LOCAL_INTERNAL:
                self.internal_token = hmac.new(url.encode(), b"internal-token", hashlib.sha256).hexdigest()
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
