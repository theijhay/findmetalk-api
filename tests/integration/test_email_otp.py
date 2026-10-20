"""FR-AUTH-1, FR-AUTH-4: durable OTPs and shared sliding-window send limits."""

import asyncio
import base64
import json
import logging
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth_crypto import decrypt_email, keyed_hash
from api.core.redis_client import redis_client
from api.db.database import AsyncSessionLocal, engine
from api.utils.settings import settings
from api.v1.models.auth.auth import AuthSession, EmailCode, User
from api.v1.services.auth import otp
from api.v1.services.auth.email import get_email_sender
from api.v1.services.auth.rate_limits import reserve_send
from api.v1.services.auth.sessions import issue_session
from main import app

pytestmark = pytest.mark.integration


@dataclass
class CapturingSender:
    deliveries: list[tuple[str, str]] = field(default_factory=list)

    async def send_code(self, *, email: str, code: str) -> None:
        self.deliveries.append((email, code))


@dataclass
class AuthHarness:
    client: httpx.AsyncClient
    sender: CapturingSender


@pytest.fixture(autouse=True)
async def clean_auth(migrated_database: None) -> AsyncIterator[None]:
    async def clean() -> None:
        async with engine.begin() as connection:
            await connection.execute(delete(AuthSession))
            await connection.execute(delete(User))
            await connection.execute(delete(EmailCode))
        keys = [key async for key in redis_client.scan_iter(match="ratelimit:otp:*")]
        if keys:
            await redis_client.delete(*keys)

    await clean()
    try:
        yield
    finally:
        await clean()


@pytest.fixture
async def auth_harness() -> AsyncIterator[AuthHarness]:
    sender = CapturingSender()
    app.dependency_overrides[get_email_sender] = lambda: sender
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 4321)), base_url="http://test"
        ) as client:
            yield AuthHarness(client, sender)
    finally:
        app.dependency_overrides.pop(get_email_sender)


async def send(harness: AuthHarness, email: str = "ada@example.com") -> str:
    response = await harness.client.post("/api/v1/auth/code", json={"email": email})
    assert response.status_code == 202, response.text
    assert response.json() == {"expires_in_seconds": 600, "resend_after_seconds": 60}
    delivered_email, code = harness.sender.deliveries[-1]
    assert delivered_email == email.strip().lower()
    assert len(code) == 6 and code.isascii() and code.isdigit()
    return code


def assert_invalid(response: httpx.Response) -> None:
    assert response.status_code == 400, response.text
    assert response.json() == {
        "error": {"code": "auth.code_invalid", "request_id": response.headers["x-request-id"]}
    }


async def allow_resend(email: str = "ada@example.com") -> None:
    async with AsyncSessionLocal.begin() as session:
        await session.execute(
            update(EmailCode)
            .where(EmailCode.email_hash == keyed_hash("email", email.encode()))
            .values(sent_at=func.clock_timestamp() - timedelta(seconds=61))
        )


async def age_rate_limit_window(key: str) -> None:
    old = datetime.now(UTC).timestamp() * 1000 - 3600001
    members: dict[str, float] = {}
    for member in await redis_client.zrange(key, 0, -1):
        assert isinstance(member, str)
        members[member] = old
    assert members
    await redis_client.zadd(key, members)


async def test_verification_creates_encrypted_identity_and_hashed_session_atomically(
    auth_harness: AuthHarness,
) -> None:
    """FR-AUTH-1: a valid code issues the exact public contract and stores no plaintext credentials."""
    code = await send(auth_harness, "  ADA@EXAMPLE.COM  ")
    response = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "Ada@Example.com", "code": code}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {
        "access_token",
        "refresh_token",
        "token_type",
        "expires_in_seconds",
        "is_new_user",
        "user",
    }
    assert {"token_type": data["token_type"]} == {"token_type": "bearer"}
    assert data["expires_in_seconds"] == 900
    assert data["is_new_user"] is True
    assert "set-cookie" not in response.headers
    refresh = data["refresh_token"]
    assert len(base64.urlsafe_b64decode(refresh + "=" * (-len(refresh) % 4))) == 32
    assert settings.JWT_SECRET is not None
    claims = jwt.decode(data["access_token"], settings.JWT_SECRET.get_secret_value(), algorithms=["HS256"])
    assert set(claims) == {"sub", "sid", "iat", "exp"}
    assert claims["exp"] - claims["iat"] == 900
    async with AsyncSessionLocal() as session:
        user = await session.get(User, UUID(claims["sub"]))
        issued = await session.get(AuthSession, UUID(claims["sid"]))
        challenge = await session.scalar(select(EmailCode))
        assert user is not None and issued is not None and challenge is not None
        assert user.email_hash == keyed_hash("email", b"ada@example.com")
        assert b"ada@example.com" not in user.email_encrypted
        assert decrypt_email(user.email_encrypted) == "ada@example.com"
        assert len(challenge.code_hash) == 32 and challenge.code_hash != code.encode()
        assert challenge.email_hash == user.email_hash
        assert challenge.consumed_at is not None
        assert 599 <= (challenge.expires_at - challenge.sent_at).total_seconds() <= 600
        assert issued.user_id == user.id
        assert issued.refresh_token_hash == keyed_hash("refresh", refresh.encode())
        assert issued.revoked_at is None
        assert timedelta(days=29) < issued.expires_at - issued.created_at <= timedelta(days=31)
        assert timedelta(days=89) < issued.absolute_expires_at - issued.created_at <= timedelta(days=91)
        assert data["user"] == {
            "id": str(user.id),
            "email": "ada@example.com",
            "status": "active",
            "role": "user",
            "age_confirmed_at": None,
            "rules_version_accepted": None,
            "recording_notice_version_accepted": None,
            "needs_acceptance": True,
            "has_profile": False,
            "created_at": user.created_at.isoformat().replace("+00:00", "Z"),
        }


async def test_new_and_existing_email_get_identical_send_responses_and_one_identity(
    auth_harness: AuthHarness,
) -> None:
    """FR-AUTH-1: sending never enumerates identities and repeat sign-in reuses the user."""
    first = await send(auth_harness)
    signed_in = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": first}
    )
    assert signed_in.status_code == 200
    await allow_resend()
    second = await send(auth_harness)
    repeated = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ADA@example.com", "code": second}
    )
    assert repeated.status_code == 200
    assert repeated.json()["is_new_user"] is False
    assert repeated.json()["user"]["id"] == signed_in.json()["user"]["id"]
    assert repeated.json()["refresh_token"] != signed_in.json()["refresh_token"]
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(AuthSession)) == 2


async def test_wrong_expired_unknown_and_reused_codes_have_one_neutral_error(
    auth_harness: AuthHarness,
) -> None:
    """FR-AUTH-1: verification failures expose neither challenge nor account state."""
    code = await send(auth_harness)
    wrong = f"{(int(code) + 1) % 1000000:06d}"
    assert_invalid(
        await auth_harness.client.post(
            "/api/v1/auth/verify", json={"email": "ada@example.com", "code": wrong}
        )
    )
    assert_invalid(
        await auth_harness.client.post(
            "/api/v1/auth/verify", json={"email": "unknown@example.com", "code": code}
        )
    )
    async with AsyncSessionLocal.begin() as session:
        await session.execute(
            update(EmailCode).values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )
    assert_invalid(
        await auth_harness.client.post("/api/v1/auth/verify", json={"email": "ada@example.com", "code": code})
    )
    await allow_resend()
    code = await send(auth_harness)
    valid = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": code}
    )
    assert valid.status_code == 200
    assert_invalid(
        await auth_harness.client.post("/api/v1/auth/verify", json={"email": "ada@example.com", "code": code})
    )


@pytest.mark.parametrize("wrong_attempts", [4, 5])
async def test_attempts_persist_and_the_fifth_wrong_attempt_kills_the_code(
    auth_harness: AuthHarness, wrong_attempts: int
) -> None:
    """FR-AUTH-4: failed attempts commit in Postgres and the cap cannot be reset by a new request."""
    code = await send(auth_harness)
    for _ in range(wrong_attempts):
        assert_invalid(
            await auth_harness.client.post(
                "/api/v1/auth/verify",
                json={"email": "ada@example.com", "code": f"{(int(code) + 1) % 1000000:06d}"},
            )
        )
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(EmailCode))
        assert row is not None and row.attempts == wrong_attempts
        assert (row.consumed_at is not None) == (wrong_attempts == 5)
    response = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": code}
    )
    if wrong_attempts == 5:
        assert_invalid(response)
    else:
        assert response.status_code == 200


async def test_concurrent_wrong_attempts_stop_at_the_durable_cap(auth_harness: AuthHarness) -> None:
    """FR-AUTH-4: concurrent verifiers cannot lose attempt increments or exceed five tries."""
    code = await send(auth_harness)
    responses = await asyncio.gather(
        *(
            auth_harness.client.post(
                "/api/v1/auth/verify",
                json={"email": "ada@example.com", "code": f"{(int(code) + 1) % 1000000:06d}"},
            )
            for _ in range(10)
        )
    )
    for response in responses:
        assert_invalid(response)
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(EmailCode))
        assert row is not None and row.attempts == 5 and row.consumed_at is not None
        assert await session.scalar(select(func.count()).select_from(User)) == 0


async def test_concurrent_correct_verifications_issue_exactly_one_session(auth_harness: AuthHarness) -> None:
    """FR-AUTH-1: one challenge can be consumed only once across concurrent transactions."""
    code = await send(auth_harness)
    responses = await asyncio.gather(
        *(
            auth_harness.client.post("/api/v1/auth/verify", json={"email": "ada@example.com", "code": code})
            for _ in range(8)
        )
    )
    assert [response.status_code for response in responses].count(200) == 1
    for response in responses:
        if response.status_code != 200:
            assert_invalid(response)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(AuthSession)) == 1


async def test_parallel_resends_within_cooldown_send_once_and_return_remaining_time(
    auth_harness: AuthHarness,
) -> None:
    """FR-AUTH-4: simultaneous sends create one code and reserve just one slot in each Redis window."""
    responses = await asyncio.gather(
        *(auth_harness.client.post("/api/v1/auth/code", json={"email": "ada@example.com"}) for _ in range(8))
    )
    assert all(response.status_code == 202 for response in responses)
    assert all(0 < response.json()["resend_after_seconds"] <= 60 for response in responses)
    assert len(auth_harness.sender.deliveries) == 1
    key = f"ratelimit:otp:send:email:{keyed_hash('email', b'ada@example.com').hex()}"
    assert await redis_client.zcard(key) == 1
    async with AsyncSessionLocal.begin() as session:
        row = await session.scalar(select(EmailCode))
        assert row is not None
        identity = row.id
        row.sent_at = datetime.now(UTC) - timedelta(seconds=20)
    response = await auth_harness.client.post("/api/v1/auth/code", json={"email": "ada@example.com"})
    assert response.status_code == 202 and 0 < response.json()["resend_after_seconds"] <= 40
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(EmailCode))
        assert row is not None and row.id == identity
    assert len(auth_harness.sender.deliveries) == 1


async def test_a_new_send_replaces_the_old_challenge_and_resets_attempts(
    auth_harness: AuthHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-AUTH-1, FR-AUTH-4: resend invalidates the old code instead of leaving multiple usable codes."""
    first = secrets.randbelow(1000000)
    values = iter((first, first, (first + 1) % 1000000))
    monkeypatch.setattr(secrets, "randbelow", lambda _: next(values))
    old = await send(auth_harness)
    assert_invalid(
        await auth_harness.client.post(
            "/api/v1/auth/verify", json={"email": "ada@example.com", "code": f"{(first + 2) % 1000000:06d}"}
        )
    )
    await allow_resend()
    new = await send(auth_harness)
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(EmailCode))
        assert row is not None and row.attempts == 0 and row.consumed_at is None
        assert await session.scalar(select(func.count()).select_from(EmailCode)) == 1
    assert_invalid(
        await auth_harness.client.post("/api/v1/auth/verify", json={"email": "ada@example.com", "code": old})
    )
    valid = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": new}
    )
    assert valid.status_code == 200


async def test_email_send_cap_returns_retry_after_without_creating_another_challenge(
    auth_harness: AuthHarness,
) -> None:
    """FR-AUTH-4: five sends are allowed per email per sliding hour, including across devices."""
    for _ in range(5):
        await send(auth_harness)
        await allow_resend()
    response = await auth_harness.client.post("/api/v1/auth/code", json={"email": "ada@example.com"})
    assert response.status_code == 429
    assert response.json() == {
        "error": {"code": "too_many_requests", "request_id": response.headers["x-request-id"]}
    }
    assert 0 < int(response.headers["retry-after"]) <= 3600
    assert len(auth_harness.sender.deliveries) == 5


async def test_ip_send_cap_holds_for_concurrent_http_requests(auth_harness: AuthHarness) -> None:
    """FR-AUTH-4: twenty sends per IP is an atomic shared cap even for distinct concurrent emails."""
    responses = await asyncio.gather(
        *(
            auth_harness.client.post("/api/v1/auth/code", json={"email": f"person{index}@example.com"})
            for index in range(30)
        )
    )
    assert [response.status_code for response in responses].count(202) == 20
    assert [response.status_code for response in responses].count(429) == 10
    assert len(auth_harness.sender.deliveries) == 20
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(EmailCode)) == 20
    for response in responses:
        if response.status_code == 429:
            assert response.json()["error"]["code"] == "too_many_requests"
            assert 0 < int(response.headers["retry-after"]) <= 3600


async def test_email_cap_is_atomic_across_ips_and_does_not_partially_charge_rejected_sends() -> None:
    """FR-AUTH-4: one email cannot evade the shared cap by sending from concurrent different IPs."""
    email_hash = keyed_hash("email", b"ada@example.com")
    results = await asyncio.gather(*(reserve_send(email_hash, f"192.0.2.{i}") for i in range(1, 21)))
    assert results.count(0) == 5
    assert all(0 < result <= 3600 for result in results if result)
    assert await redis_client.zcard(f"ratelimit:otp:send:email:{email_hash.hex()}") == 5
    for index, result in enumerate(results, start=1):
        key = f"ratelimit:otp:send:ip:{keyed_hash('ip', f'192.0.2.{index}'.encode()).hex()}"
        assert await redis_client.zcard(key) == (0 if result else 1)


async def test_sliding_windows_expire_old_entries_have_ttls_and_never_store_raw_ips() -> None:
    """FR-AUTH-4: old sends age out independently and Redis keys have bounded retention without PII."""
    email_hash = keyed_hash("email", b"ada@example.com")
    client_ip = "192.0.2.1"
    keys = [
        f"ratelimit:otp:send:email:{email_hash.hex()}",
        f"ratelimit:otp:send:ip:{keyed_hash('ip', client_ip.encode()).hex()}",
    ]
    now_ms = datetime.now(UTC).timestamp() * 1000
    for key in keys:
        await redis_client.zadd(key, {uuid4().hex: now_ms - 3600001, uuid4().hex: now_ms - 10000})
    assert await reserve_send(email_hash, client_ip) == 0
    for key in keys:
        assert await redis_client.zcard(key) == 2
        assert 0 < await redis_client.pttl(key) <= 3600000
        assert client_ip not in key and "ada@example.com" not in key


async def test_concurrent_verification_email_cap_checks_before_a_correct_code_and_ages_out(
    auth_harness: AuthHarness,
) -> None:
    """FR-AUTH-4: ten attempts per email are allowed; a limited correct code stays usable after aging."""
    guess = f"{secrets.randbelow(1000000):06d}"
    responses = await asyncio.gather(
        *(
            auth_harness.client.post("/api/v1/auth/verify", json={"email": "ada@example.com", "code": guess})
            for _ in range(10)
        )
    )
    for response in responses:
        assert_invalid(response)
    code = await send(auth_harness)
    for candidate in (code, f"{(int(code) + 1) % 1000000:06d}"):
        response = await auth_harness.client.post(
            "/api/v1/auth/verify", json={"email": "ADA@example.com", "code": candidate}
        )
        assert response.status_code == 429
        assert response.json() == {
            "error": {"code": "too_many_requests", "request_id": response.headers["x-request-id"]}
        }
        assert 0 < int(response.headers["retry-after"]) <= 3600
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(EmailCode))
        assert row is not None and row.attempts == 0 and row.consumed_at is None
        assert await session.scalar(select(func.count()).select_from(User)) == 0
    key = f"ratelimit:otp:verify:email:{keyed_hash('email', b'ada@example.com').hex()}"
    assert await redis_client.zcard(key) == 10
    assert 0 < await redis_client.pttl(key) <= 3600000
    await age_rate_limit_window(key)
    response = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": code}
    )
    assert response.status_code == 200
    assert await redis_client.zcard(key) == 1


async def test_concurrent_verification_ip_cap_cannot_be_evaded_with_forwarded_headers(
    auth_harness: AuthHarness,
) -> None:
    """FR-AUTH-4: the 31st attempt from one peer is refused across emails and untrusted IP headers."""
    guess = f"{secrets.randbelow(1000000):06d}"
    responses = await asyncio.gather(
        *(
            auth_harness.client.post(
                "/api/v1/auth/verify",
                json={"email": f"person{index}@example.com", "code": guess},
                headers={"X-Forwarded-For": f"198.51.100.{index}", "X-FMT-Client-IP": f"198.51.100.{index}"},
            )
            for index in range(30)
        )
    )
    for response in responses:
        assert_invalid(response)
    code = await send(auth_harness)
    response = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": code}
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_requests"
    assert 0 < int(response.headers["retry-after"]) <= 3600
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(EmailCode))
        assert row is not None and row.attempts == 0 and row.consumed_at is None
    key = f"ratelimit:otp:verify:ip:{keyed_hash('ip', b'192.0.2.1').hex()}"
    assert await redis_client.zcard(key) == 30
    assert 0 < await redis_client.pttl(key) <= 3600000
    await age_rate_limit_window(key)
    response = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": code}
    )
    assert response.status_code == 200
    assert await redis_client.zcard(key) == 1


async def test_successful_and_failed_verifications_both_count_towards_the_email_limit(
    auth_harness: AuthHarness,
) -> None:
    """FR-AUTH-4: successful verification consumes a slot just like a wrong or reused code."""
    code = await send(auth_harness)
    response = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": code}
    )
    assert response.status_code == 200
    for _ in range(9):
        assert_invalid(
            await auth_harness.client.post(
                "/api/v1/auth/verify", json={"email": "ada@example.com", "code": code}
            )
        )
    response = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": code}
    )
    assert response.status_code == 429
    key = f"ratelimit:otp:verify:email:{keyed_hash('email', b'ada@example.com').hex()}"
    assert await redis_client.zcard(key) == 10


async def test_failed_session_issuance_rolls_back_user_and_consumption(
    auth_harness: AuthHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-AUTH-1: OTP consumption, user creation and session issuance succeed or roll back together."""
    code = await send(auth_harness)

    async def fail_after_issuance(session: AsyncSession, user: User) -> None:
        await issue_session(session, user)
        raise RuntimeError("issuance failed")

    monkeypatch.setattr(otp, "issue_session", fail_after_issuance)
    response = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": code}
    )
    assert response.status_code == 500
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(EmailCode))
        assert row is not None and row.consumed_at is None
        assert await session.scalar(select(func.count()).select_from(User)) == 0
        assert await session.scalar(select(func.count()).select_from(AuthSession)) == 0
    monkeypatch.setattr(otp, "issue_session", issue_session)
    response = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": code}
    )
    assert response.status_code == 200


async def test_authentication_lifetimes_and_attempt_caps_follow_settings(
    auth_harness: AuthHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-AUTH-1, FR-AUTH-4: code and session lifetimes and the verification cap are configurable."""
    monkeypatch.setattr(settings, "OTP_TTL_SECONDS", 30)
    monkeypatch.setattr(settings, "OTP_RESEND_SECONDS", 10)
    monkeypatch.setattr(settings, "OTP_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "ACCESS_TOKEN_TTL_SECONDS", 45)
    monkeypatch.setattr(settings, "REFRESH_TOKEN_IDLE_DAYS", 3)
    monkeypatch.setattr(settings, "SESSION_MAX_AGE_DAYS", 2)
    response = await auth_harness.client.post("/api/v1/auth/code", json={"email": "ada@example.com"})
    assert response.status_code == 202
    assert response.json() == {"expires_in_seconds": 30, "resend_after_seconds": 10}
    code = auth_harness.sender.deliveries[-1][1]
    for _ in range(2):
        assert_invalid(
            await auth_harness.client.post(
                "/api/v1/auth/verify",
                json={"email": "ada@example.com", "code": f"{(int(code) + 1) % 1000000:06d}"},
            )
        )
    assert_invalid(
        await auth_harness.client.post("/api/v1/auth/verify", json={"email": "ada@example.com", "code": code})
    )
    response = await auth_harness.client.post("/api/v1/auth/code", json={"email": "grace@example.com"})
    assert response.status_code == 202
    code = auth_harness.sender.deliveries[-1][1]
    response = await auth_harness.client.post(
        "/api/v1/auth/verify", json={"email": "grace@example.com", "code": code}
    )
    assert response.status_code == 200 and response.json()["expires_in_seconds"] == 45
    assert settings.JWT_SECRET is not None
    claims = jwt.decode(
        response.json()["access_token"], settings.JWT_SECRET.get_secret_value(), algorithms=["HS256"]
    )
    assert claims["exp"] - claims["iat"] == 45
    async with AsyncSessionLocal() as session:
        row = await session.get(AuthSession, UUID(claims["sid"]))
        assert row is not None and row.expires_at == row.absolute_expires_at
        assert timedelta(days=1) < row.absolute_expires_at - row.created_at <= timedelta(days=3)


async def test_requests_validation_and_provider_failure_never_log_emails_codes_or_ips(
    auth_harness: AuthHarness, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-AUTH-1, FR-AUTH-4: success and failure logs retain outcomes without PII or code material."""
    with caplog.at_level(logging.INFO):
        code = await send(auth_harness)
        response = await auth_harness.client.post(
            "/api/v1/auth/verify", json={"email": "ada@example.com", "code": code}
        )
        assert response.status_code == 200
        validation = await auth_harness.client.post(
            "/api/v1/auth/code", json={"email": "private invalid input"}
        )
        assert validation.status_code == 422
        monkeypatch.setattr(
            auth_harness.sender,
            "send_code",
            AsyncMock(side_effect=RuntimeError(f"ada@example.com {code} 192.0.2.1")),
        )
        await allow_resend()
        failure = await auth_harness.client.post("/api/v1/auth/code", json={"email": "ada@example.com"})
        assert failure.status_code == 500
    rendered = json.dumps([record.__dict__ for record in caplog.records], default=str)
    for sensitive in ("ada@example.com", code, "192.0.2.1", "private invalid input"):
        assert sensitive not in rendered
        assert sensitive not in failure.text + validation.text
