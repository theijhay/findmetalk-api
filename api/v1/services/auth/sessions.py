"""Issue and rotate session token pairs (FR-AUTH-1, FR-AUTH-3)."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from fastapi import HTTPException
from sqlalchemy import select, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth_crypto import decrypt_email, keyed_hash
from api.db.database import AsyncSessionLocal
from api.utils.settings import settings
from api.v1.models.auth.auth import AuthSession, SpentRefreshToken, User
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


def _token_pair(user_id: UUID, session_id: UUID, refresh: str, now: datetime) -> TokenPair:
    secret = settings.JWT_SECRET
    if secret is None:
        raise RuntimeError("auth.jwt_key_missing")
    token = jwt.encode(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "iat": now,
            "exp": now + timedelta(seconds=settings.ACCESS_TOKEN_TTL_SECONDS),
        },
        secret.get_secret_value(),
        algorithm="HS256",
    )
    return TokenPair(
        access_token=token, refresh_token=refresh, expires_in_seconds=settings.ACCESS_TOKEN_TTL_SECONDS
    )


async def issue_session(session: AsyncSession, user: User) -> TokenPair:
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
    return _token_pair(user.id, identity, refresh, now)


async def rotate_refresh_token(refresh: str) -> TokenPair:
    token_hash = keyed_hash("refresh", refresh.encode())
    pair = None
    async with AsyncSessionLocal.begin() as db:
        # A single snapshot sees the token either as current or as spent,
        # including when another transaction is rotating it concurrently.
        session_id = await db.scalar(
            union_all(
                select(AuthSession.id).where(AuthSession.refresh_token_hash == token_hash),
                select(SpentRefreshToken.session_id).where(SpentRefreshToken.token_hash == token_hash),
            )
        )
        if session_id is not None:
            row = await db.get(AuthSession, session_id, with_for_update=True)
            if row is not None and row.revoked_at is None:
                now = datetime.now(UTC)
                if row.refresh_token_hash != token_hash:
                    row.revoked_at = now
                elif now < min(row.expires_at, row.absolute_expires_at):
                    user = await db.get(User, row.user_id)
                    if user is not None and user.status != "banned":
                        new_refresh = secrets.token_urlsafe(32)
                        db.add(SpentRefreshToken(token_hash=token_hash, session_id=row.id))
                        row.refresh_token_hash = keyed_hash("refresh", new_refresh.encode())
                        row.last_used_at = now
                        row.expires_at = min(
                            now + timedelta(days=settings.REFRESH_TOKEN_IDLE_DAYS), row.absolute_expires_at
                        )
                        pair = _token_pair(user.id, row.id, new_refresh, now)
    # Reuse must commit its revocation before the failure leaves the service.
    if pair is None:
        raise HTTPException(401, detail="auth.session_expired")
    return pair


def access_identity(token: str) -> tuple[UUID, UUID]:
    secret = settings.JWT_SECRET
    if secret is None:
        raise RuntimeError("auth.jwt_key_missing")
    try:
        claims = jwt.decode(
            token,
            secret.get_secret_value(),
            algorithms=["HS256"],
            options={"require": ["sub", "sid", "iat", "exp"]},
        )
        subject, session_id = claims["sub"], claims["sid"]
        if not isinstance(subject, str) or not isinstance(session_id, str):
            raise ValueError
        return UUID(subject), UUID(session_id)
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
        raise HTTPException(401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"}) from None


async def active_user(user_id: UUID, session_id: UUID) -> User:
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as db:
        user = await db.scalar(
            select(User)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(
                User.id == user_id,
                User.status != "banned",
                AuthSession.id == session_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
                AuthSession.absolute_expires_at > now,
            )
        )
    if user is None:
        raise HTTPException(401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})
    return user


async def revoke_sessions(user_id: UUID, *, session_id: UUID | None = None) -> None:
    async with AsyncSessionLocal.begin() as db:
        statement = update(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        )
        if session_id is not None:
            statement = statement.where(AuthSession.id == session_id)
        await db.execute(statement.values(revoked_at=datetime.now(UTC)))
