"""Application configuration.

This module is the single source of truth for the app's display name and
runtime behavior. Per project conventions, the user-facing brand name
("Notabula") lives ONLY in the APP_NAME setting (sourced from the
environment) — never inline in code, schemas, or routes.
"""

from functools import lru_cache
from urllib.parse import quote

from pydantic import Field, PostgresDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Branding (display only) ---------------------------------------
    # The ONLY place the brand name lives. Renaming the app = change this.
    app_name: str = Field(default="Notabula", alias="APP_NAME")
    app_tagline: str = Field(default="Your notes, on your server", alias="APP_TAGLINE")
    app_url: str = Field(default="http://localhost:8200", alias="APP_URL")
    support_email: str = Field(default="hello@example.com", alias="SUPPORT_EMAIL")

    # --- Deployment ----------------------------------------------------
    environment: str = Field(default="development", alias="ENVIRONMENT")
    debug: bool = Field(default=False, alias="DEBUG")
    # Echo every SQL statement — very noisy; off even in debug unless requested.
    sql_echo: bool = Field(default=False, alias="SQL_ECHO")

    # --- Security ------------------------------------------------------
    secret_key: str = Field(default="dev-insecure-change-me", alias="SECRET_KEY")
    session_ttl_days: int = Field(default=90, alias="SESSION_TTL_DAYS")
    # Registration is open only while the instance has zero users: the first
    # account becomes admin and the door closes. Admins add everyone else
    # (Settings → Users). No env toggle — it can't be left open by accident.
    min_password_length: int = Field(default=8, alias="MIN_PASSWORD_LENGTH")
    # Days a note stays in Recently Deleted before the daily purge removes it.
    purge_after_days: int = Field(default=30, alias="PURGE_AFTER_DAYS")

    # --- Datastore -----------------------------------------------------
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://notesapp:notesapp@localhost:5433/notesapp",
        alias="DATABASE_URL",
    )

    # Alternative to DATABASE_URL: discrete parts, URL-encoded here so the
    # password may contain any character (@, :, /, %, …). Deployments set
    # these (see docker-compose.portainer.yml); DATABASE_URL is ignored
    # whenever DB_PASSWORD is present.
    db_host: str | None = Field(default=None, alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")
    db_user: str = Field(default="notesapp", alias="DB_USER")
    db_password: str | None = Field(default=None, alias="DB_PASSWORD")
    db_name: str = Field(default="notesapp", alias="DB_NAME")

    @model_validator(mode="after")
    def assemble_database_url(self) -> "Settings":
        if self.db_password is not None:
            host = self.db_host or "localhost"
            self.database_url = (
                f"postgresql+asyncpg://{quote(self.db_user, safe='')}:"
                f"{quote(self.db_password, safe='')}@{host}:{self.db_port}/"
                f"{quote(self.db_name, safe='')}"
            )
        if not self.vapid_subject:
            self.vapid_subject = self.app_url
        return self

    # --- Push notifications ---------------------------------------------
    # APNs (native iOS app) goes through the self-hosted push-relay; unset
    # means no native push. Web Push (installed PWA) needs no config: a
    # VAPID keypair is auto-generated and persisted under MEDIA_ROOT.
    push_relay_url: str | None = Field(default=None, alias="PUSH_RELAY_URL")
    push_relay_api_key: str | None = Field(default=None, alias="PUSH_RELAY_API_KEY")
    apns_bundle_id: str = Field(default="app.jwapps.notabula", alias="APNS_BUNDLE_ID")
    vapid_subject: str = Field(default="", alias="VAPID_SUBJECT")

    # --- Media / file storage ------------------------------------------
    # Where note attachments live (a persisted Docker volume in compose),
    # served back at /media. Used from Phase 2 onward.
    media_root: str = Field(default="/app/media", alias="MEDIA_ROOT")
    max_upload_mb: int = Field(default=20, alias="MAX_UPLOAD_MB")

    # --- CORS ----------------------------------------------------------
    cors_origins: list[str] = Field(
        default=["http://localhost:5175"],
        alias="CORS_ORIGINS",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
