from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_env: str = "local"  # local | test | staging | production
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/comms"

    jwt_secret: str = "local-dev-only-secret-change-me-0123456789"
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
    internal_token: str = "local-internal-token"
    internal_oidc_audience: str = ""

    upload_dir: str = "./.uploads"
    default_country_code: str = "+91"
    default_timezone: str = "Asia/Kolkata"

    cors_origins: str = "http://localhost:3000"

    whatsapp_app_secret_ref: str = ""
    whatsapp_verify_token: str = "local-verify-token"

    sending_lease_seconds: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()
