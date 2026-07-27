from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    deployment_mode: Literal["local", "private", "public"] = Field(
        default="local",
        validation_alias=AliasChoices("ALPHAWAVE_DEPLOYMENT_MODE", "DEPLOYMENT_MODE"),
    )
    app_host: str = "127.0.0.1"
    app_port: int = 8711
    app_dev_endpoints_enabled: bool = False
    app_timezone: str = "UTC"
    app_daily_briefing_time: str = "13:00"
    app_daily_briefing_latest_time: str = "19:00"
    daily_briefing_enabled: bool = True
    daily_briefing_time: str = "13:00"
    daily_briefing_late_cutoff: str = "19:00"
    daily_briefing_timezone: str = "UTC"
    daily_briefing_send_on_startup: bool = True
    daily_briefing_max_deep_work: int = 3
    daily_briefing_max_quick_tasks: int = 5
    daily_briefing_max_reviews: int = 5
    daily_briefing_max_attention: int = 5
    daily_briefing_max_inbox: int = 5
    daily_briefing_max_perpetuals: int = 3
    checkins_enabled: bool = True
    in_progress_stale_days: int = 2
    perpetual_max_per_day: int = 3

    database_url: str = "sqlite:///./data/alphawave-taskd.sqlite"
    log_file: str = "./logs/alphawave-taskd.log"
    instance_lock_path: str = Field(
        default="./data/alphawave-taskd.lock",
        validation_alias=AliasChoices("ALPHAWAVE_INSTANCE_LOCK_PATH", "INSTANCE_LOCK_PATH"),
    )
    instance_lock_test_bypass: bool = Field(
        default=False,
        validation_alias="ALPHAWAVE_INSTANCE_LOCK_TEST_BYPASS",
    )

    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_allowed_user_id: str = ""
    telegram_poll_interval_seconds: float = 3.0

    reminders_enabled: bool = True
    reminders_poll_interval_seconds: float = 30.0

    trello_enabled: bool = False
    trello_api_key: str = ""
    trello_token: str = ""
    trello_member_id: str = ""
    trello_board_alpha_id: str = ""
    trello_board_beta_id: str = ""
    trello_alpha_board_id: str = ""
    trello_beta_board_id: str = ""
    trello_sync_interval_minutes: int = 15
    trello_write_enabled: bool = False
    trello_confirmation_ttl_hours: int = 24

    openai_timeout_seconds: int = 25

    backup_enabled: bool = True
    backup_retention_days: int = 30

    auth_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("ALPHAWAVE_AUTH_ENABLED", "AUTH_ENABLED"),
    )
    auth_cookie_name: str = Field(
        default="alphawave_session",
        validation_alias=AliasChoices("ALPHAWAVE_AUTH_COOKIE_NAME", "AUTH_COOKIE_NAME"),
    )
    auth_cookie_secure: bool = Field(
        default=False,
        validation_alias=AliasChoices("ALPHAWAVE_AUTH_COOKIE_SECURE", "AUTH_COOKIE_SECURE"),
    )
    auth_session_ttl_hours: int = Field(
        default=168,
        validation_alias=AliasChoices("ALPHAWAVE_AUTH_SESSION_TTL_HOURS", "AUTH_SESSION_TTL_HOURS"),
    )
    auth_pbkdf2_iterations: int = 210_000
    alphawave_secret_encryption_key: str = ""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @model_validator(mode="after")
    def validate_deployment_security(self) -> "Settings":
        validate_deployment_security(self)
        return self

    @property
    def docs_enabled(self) -> bool:
        return self.deployment_mode == "local"

    @property
    def docs_url(self) -> str | None:
        return "/docs" if self.docs_enabled else None

    @property
    def redoc_url(self) -> str | None:
        return "/redoc" if self.docs_enabled else None

    @property
    def openapi_url(self) -> str | None:
        return "/openapi.json" if self.docs_enabled else None

    @property
    def allowed_origins(self) -> list[str]:
        return [
            f"http://{self.app_host}:{self.app_port}",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        raw = self.database_url.removeprefix(prefix)
        path = Path(raw)
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path

    @property
    def resolved_instance_lock_path(self) -> Path:
        path = Path(self.instance_lock_path)
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path

    @property
    def sqlalchemy_database_url(self) -> str:
        sqlite_path = self.sqlite_path
        if sqlite_path:
            return f"sqlite:///{sqlite_path}"
        return self.database_url

    @property
    def resolved_trello_alpha_board_id(self) -> str:
        return self.trello_board_alpha_id or self.trello_alpha_board_id

    @property
    def resolved_trello_beta_board_id(self) -> str:
        return self.trello_board_beta_id or self.trello_beta_board_id


def validate_deployment_security(config: Settings) -> None:
    mode = config.deployment_mode
    if mode in {"private", "public"} and not config.auth_enabled:
        raise ValueError(f"authentication must be enabled in {mode} deployment mode")
    if mode in {"private", "public"} and config.app_dev_endpoints_enabled:
        raise ValueError(f"development endpoints must be disabled in {mode} deployment mode")
    if mode == "public" and not config.auth_cookie_secure:
        raise ValueError("secure authentication cookies are required in public deployment mode")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
