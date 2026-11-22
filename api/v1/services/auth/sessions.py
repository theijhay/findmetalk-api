"""Issue a session and token pair atomically with OTP verification (FR-AUTH-1)."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth_crypto import decrypt_email, keyed_hash
from api.utils.settings import settings
from api.v1.models.auth.auth import AuthSession, User
from api.v1.schemas.auth.auth import TokenPair, UserMe


def user_me(user: User) -> UserMe:
    return UserMe.model_validate(
        {
            "id": user.id,
            "email": decrypt_email(user.email_encrypted),
            "status": user.status,
            "role": user.role,
            "created_at": user.created_at,
        }
    )


async def issue_session(session: AsyncSession, user: User) -> TokenPair:
    secret = settings.JWT_SECRET
    if secret is None:
        raise RuntimeError("auth.jwt_key_missing")
    now = datetime.now(UTC)
    absolute = now + timedelta(days=settings.SESSION_MAX_AGE_DAYS)
    refresh = secrets.token_urlsafe(32)
    identity = uuid4()
    session.add(
        AuthSession(
            id=identity,
            user_id=user.id,
            refresh_token_hash=keyed_hash("refresh", refresh.encode()),
            expires_at=min(now + timedelta(days=settings.REFRESH_TOKEN_IDLE_DAYS), absolute),
            absolute_expires_at=absolute,
        )
    )
    await session.flush()
    token = jwt.encode(
        {
            "sub": str(user.id),
            "sid": str(identity),
            "iat": now,
            "exp": now + timedelta(seconds=settings.ACCESS_TOKEN_TTL_SECONDS),
        },
        secret.get_secret_value(),
        algorithm="HS256",
    )
    return TokenPair(
        access_token=token, refresh_token=refresh, expires_in_seconds=settings.ACCESS_TOKEN_TTL_SECONDS
    )
