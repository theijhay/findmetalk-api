import base64
import binascii
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment settings, read from the environment (or `.env` locally).

    A new setting is added here, to `.env.example` with a comment saying what
    it gates, and to the deployment, in the same pull request.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

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

    ENCRYPTION_KEY: SecretStr | None = None
    EMAIL_HASH_KEY: SecretStr | None = None
    JWT_SECRET: SecretStr | None = None
    TRUSTED_PROXY_SECRET: SecretStr | None = None
    EMAIL_PROVIDER: Literal["console"] = "console"
    OTP_TTL_SECONDS: int = Field(default=600, ge=1)
    OTP_MAX_ATTEMPTS: int = Field(default=5, ge=1)
    OTP_RESEND_SECONDS: int = Field(default=60, ge=1)
    OTP_EMAIL_LIMIT_PER_HOUR: int = Field(default=5, ge=1)
    OTP_IP_LIMIT_PER_HOUR: int = Field(default=20, ge=1)
    OTP_VERIFY_PER_EMAIL_PER_HOUR: int = Field(default=10, ge=1)
    OTP_VERIFY_PER_IP_PER_HOUR: int = Field(default=30, ge=1)
    ACCESS_TOKEN_TTL_SECONDS: int = Field(default=900, ge=1)
    REFRESH_TOKEN_IDLE_DAYS: int = Field(default=30, ge=1)
    SESSION_MAX_AGE_DAYS: int = Field(default=90, ge=1)

    @field_validator("ENCRYPTION_KEY", "EMAIL_HASH_KEY", "JWT_SECRET", "TRUSTED_PROXY_SECRET", mode="before")
    @classmethod
    def empty_secret_is_unset(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("ENCRYPTION_KEY", "EMAIL_HASH_KEY", "TRUSTED_PROXY_SECRET")
    @classmethod
    def keys_are_base64_encoded_32_bytes(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                decoded = base64.b64decode(value.get_secret_value(), altchars=b"-_", validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("auth.key_invalid") from None
            if len(decoded) != 32:
                raise ValueError("auth.key_invalid")
        return value

    @field_validator("JWT_SECRET")
    @classmethod
    def jwt_key_has_at_least_32_bytes(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < 32:
            raise ValueError("auth.key_invalid")
        return value

    def require_auth_keys(self) -> None:
        # Importing the image for validation needs no secrets. Starting the
        # service must refuse missing keys before it accepts sign-in traffic.
        if self.ENCRYPTION_KEY is None or self.EMAIL_HASH_KEY is None or self.JWT_SECRET is None:
            raise ValueError("auth.keys_missing")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


settings = Settings()
