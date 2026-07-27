"""Central app settings. Reads all env vars in one place.

Usage:
    from src.settings import settings
    settings.postgres_host
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- App ----
    app_name: str = "simit-be"
    env: str = "development"
    debug: bool = True
    app_port: int = 8888
    api_prefix: str = "/v1/api"

    # ---- Auth (JWT bearer tokens) ----
    jwt_secret_key: str = "change-me-in-prod"
    jwt_expire_minutes: int = 60 * 24

    # ---- Postgres ----
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "simit"
    postgres_user: str = "simit"
    postgres_password: str = "changeme"

    # ---- SMTP (pipeline notification emails: EIC/SC/author) ----
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@simit.local"
    # Display name shown to recipients. Kept apart from smtp_from because that
    # value is also the envelope sender, which must be a bare address — mail
    # servers verify it and refuse anything they cannot match to the account.
    smtp_from_name: str = ""

    # ---- Storage (S3-compatible: AWS S3, MinIO, Cloudflare R2, ...) ----
    # All empty by default — placeholders until real credentials exist.
    storage_base_url: str = ""
    storage_bucket: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_region: str = "auto"

    # ---- Uploads ----
    # Max accepted PDF size for POST /articles/{id}/upload.
    max_upload_mb: int = 10


# Singleton — import this everywhere.
settings = Settings()
