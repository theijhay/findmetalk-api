from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment settings, read from the environment (or `.env` locally).

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
