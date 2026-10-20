from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import Field, JsonValue
from pydantic_settings import BaseSettings, SettingsConfigDict

# Runtime values live in Postgres; these are the fallback values for missing
# rows, not environment overrides. Keep the cache within the update window.
CONFIG_CACHE_TTL_SECONDS = 30
RUNTIME_CONFIG_DEFAULTS: Mapping[str, JsonValue] = MappingProxyType(
    {
        "calls.ring_timeout_seconds": 20,
        "calls.recall_cooldown_seconds": 3600,
        "calls.request_burst_per_hour": 30,
        "calls.max_duration_seconds": 21600,
        "calls.reconnect_window_seconds": 15,
        "presence.heartbeat_ttl_seconds": 30,
        "circles.max_size": 8,
        "safety.autoflag_reporter_threshold": 3,
        "safety.autoflag_window_hours": 24,
        "recordings.retention_days": 14,
        "recordings.enabled": {"default": {"call": True, "circle": True}, "countries": {}},
        "consent.rules_version": "1",
        "consent.recording_notice_version": "1",
    }
)
RUNTIME_CONFIG_INTEGER_RANGES: Mapping[str, tuple[int, int]] = MappingProxyType(
    {
        "calls.ring_timeout_seconds": (5, 60),
        "calls.recall_cooldown_seconds": (0, 604800),
        "calls.request_burst_per_hour": (1, 1000),
        "calls.max_duration_seconds": (600, 86400),
        "calls.reconnect_window_seconds": (5, 120),
        "presence.heartbeat_ttl_seconds": (10, 120),
        "circles.max_size": (3, 50),
        "safety.autoflag_reporter_threshold": (2, 50),
        "safety.autoflag_window_hours": (1, 720),
        "recordings.retention_days": (1, 365),
    }
)


class Settings(BaseSettings):
    """Every runtime setting, read from the environment (or `.env` locally).

    A new setting is added here, to `.env.example` with a comment saying what
    it gates, and to the deployment, in the same pull request.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # The product name is read from here and never written into code or copy
    # (spec: "Never hardcode the name").
    APP_NAME: str = "FindMeTalk"
    APP_ENV: Literal["development", "test", "staging", "production"] = "development"
    DEBUG: bool = False
    APP_PORT: int = 8000
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Comma-separated browser origins allowed to call the API (web and admin).
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Database. Postgres is the source of truth for everything durable (TR-1).
    DB_URL: str
    DB_POOL_SIZE: int = Field(default=5, ge=1, le=50)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0, le=100)
    DB_POOL_TIMEOUT: int = Field(default=30, ge=1, le=300)
    DB_POOL_RECYCLE: int = Field(default=1800, ge=60)

    # Redis holds presence, call locks and guardrail counters (TR-1, TR-3).
    REDIS_URL: str

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


settings = Settings()
