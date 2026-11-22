"""Email OTP and session issuance contract (FR-AUTH-1, FR-AUTH-4)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StrictStr, field_validator
from pydantic_core import PydanticCustomError

from api.core.auth_crypto import canonical_email


class CodeRequest(BaseModel):
    email: StrictStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        try:
            return canonical_email(value)
        except ValueError:
            raise PydanticCustomError("auth.email_invalid", "auth.email_invalid") from None


class VerifyRequest(CodeRequest):
    code: Annotated[StrictStr, Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")]


class CodeResponse(BaseModel):
    expires_in_seconds: int
    resend_after_seconds: int


class UserMe(BaseModel):
    id: UUID
    email: str
    status: Literal["active", "suspended", "banned"]
    role: Literal["user", "moderator", "owner"]
    age_confirmed_at: datetime | None = None
    rules_version_accepted: str | None = None
    recording_notice_version_accepted: str | None = None
    needs_acceptance: bool = True
    has_profile: bool = False
    created_at: datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - protocol discriminator, not a credential
    expires_in_seconds: int


class VerifyResponse(TokenPair):
    is_new_user: bool
    user: UserMe
