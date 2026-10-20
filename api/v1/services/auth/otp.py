"""Single-use OTPs with durable attempts and serialized issuance/verification (FR-AUTH-1, FR-AUTH-4)."""

import hmac
import math
import secrets
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth_crypto import canonical_email, encrypt_email, keyed_hash
from api.db.database import AsyncSessionLocal
from api.utils.settings import settings
from api.v1.models.auth.auth import EmailCode, User
from api.v1.schemas.auth.auth import CodeResponse, VerifyResponse
from api.v1.services.auth.email import EmailSender
from api.v1.services.auth.rate_limits import reserve_send, reserve_verification
from api.v1.services.auth.sessions import issue_session, user_me


def _code_hash(identity: UUID, email_hash: bytes, code: str) -> bytes:
    return keyed_hash("otp", identity.bytes + email_hash + code.encode())


async def _lock_email(session: AsyncSession, email_hash: bytes) -> datetime:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"auth:otp:{email_hash.hex()}"}
    )
    return cast(datetime, await session.scalar(select(func.clock_timestamp())))


async def request_code(email: str, client_ip: str, sender: EmailSender) -> CodeResponse:
    email = canonical_email(email)
    email_hash = keyed_hash("email", email.encode())
    async with AsyncSessionLocal.begin() as session:
        now = await _lock_email(session, email_hash)
        challenge = await session.scalar(select(EmailCode).where(EmailCode.email_hash == email_hash))
        if challenge is not None:
            remaining = math.ceil(
                (challenge.sent_at + timedelta(seconds=settings.OTP_RESEND_SECONDS) - now).total_seconds()
            )
            if remaining > 0:
                return CodeResponse(
                    expires_in_seconds=max(0, math.ceil((challenge.expires_at - now).total_seconds())),
                    resend_after_seconds=remaining,
                )
        retry_after = await reserve_send(email_hash, client_ip)
        if retry_after:
            raise HTTPException(429, detail="too_many_requests", headers={"Retry-After": str(retry_after)})
        code = f"{secrets.randbelow(1000000):06d}"
        # A resend must also invalidate the previous six-digit value if the
        # random generator happens to pick it again.
        while challenge is not None and hmac.compare_digest(
            challenge.code_hash, _code_hash(challenge.id, email_hash, code)
        ):
            code = f"{secrets.randbelow(1000000):06d}"
        identity = uuid4()
        hashed = _code_hash(identity, email_hash, code)
        expires = now + timedelta(seconds=settings.OTP_TTL_SECONDS)
        if challenge is None:
            session.add(
                EmailCode(
                    id=identity, email_hash=email_hash, code_hash=hashed, sent_at=now, expires_at=expires
                )
            )
        else:
            challenge.id, challenge.code_hash = identity, hashed
            challenge.sent_at, challenge.expires_at = now, expires
            challenge.attempts, challenge.consumed_at = 0, None
        await session.flush()
        await sender.send_code(email=email, code=code)
    return CodeResponse(
        expires_in_seconds=settings.OTP_TTL_SECONDS, resend_after_seconds=settings.OTP_RESEND_SECONDS
    )


async def verify_code(email: str, code: str, client_ip: str) -> VerifyResponse:
    email = canonical_email(email)
    email_hash = keyed_hash("email", email.encode())
    retry_after = await reserve_verification(email_hash, client_ip)
    if retry_after:
        raise HTTPException(429, detail="too_many_requests", headers={"Retry-After": str(retry_after)})
    result: VerifyResponse | None = None
    async with AsyncSessionLocal.begin() as session:
        now = await _lock_email(session, email_hash)
        challenge = await session.scalar(select(EmailCode).where(EmailCode.email_hash == email_hash))
        if (
            challenge is not None
            and challenge.consumed_at is None
            and challenge.expires_at > now
            and challenge.attempts < settings.OTP_MAX_ATTEMPTS
        ):
            if not hmac.compare_digest(challenge.code_hash, _code_hash(challenge.id, email_hash, code)):
                challenge.attempts += 1
                if challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
                    challenge.consumed_at = now
            else:
                challenge.consumed_at = now
                user = await session.scalar(select(User).where(User.email_hash == email_hash))
                is_new = user is None
                if user is None:
                    user = User(email_encrypted=encrypt_email(email), email_hash=email_hash)
                    session.add(user)
                    await session.flush()
                tokens = await issue_session(session, user)
                result = VerifyResponse(**tokens.model_dump(), is_new_user=is_new, user=user_me(user))
    # Failed attempts must commit before the neutral error is raised.
    if result is None:
        raise HTTPException(400, detail="auth.code_invalid")
    return result
