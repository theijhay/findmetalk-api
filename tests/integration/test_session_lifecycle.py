"""FR-AUTH-3: refresh rotation and logout against durable sessions."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update

from api.core.auth_crypto import encrypt_email, keyed_hash
from api.db.database import AsyncSessionLocal
from api.v1.models.auth.auth import AuthSession, User
from api.v1.schemas.auth.auth import TokenPair
from api.v1.services.auth.sessions import issue_session
from main import app

pytestmark = pytest.mark.integration


@pytest.fixture
async def account(migrated_database: None) -> AsyncIterator[User]:
    email = f"{uuid4().hex}@example.com"
    async with AsyncSessionLocal.begin() as db:
        user = User(email_encrypted=encrypt_email(email), email_hash=keyed_hash("email", email.encode()))
        db.add(user)
    try:
        yield user
    finally:
        async with AsyncSessionLocal.begin() as db:
            stored = await db.get(User, user.id)
            if stored is not None:
                await db.delete(stored)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def new_device(user: User) -> TokenPair:
    async with AsyncSessionLocal.begin() as db:
        return await issue_session(db, user)


def bearer(pair: TokenPair) -> dict[str, str]:
    return {"Authorization": f"Bearer {pair.access_token}"}


async def test_refresh_reuse_revokes_only_the_rotated_session(
    account: User, client: httpx.AsyncClient
) -> None:
    """FR-AUTH-3: replay voids the entire token family and immediately invalidates access tokens."""
    original = await new_device(account)
    different_device = await new_device(account)
    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": original.refresh_token})
    assert first.status_code == 200
    rotated = TokenPair.model_validate(first.json())
    assert rotated.refresh_token != original.refresh_token
    second = await client.post("/api/v1/auth/refresh", json={"refresh_token": rotated.refresh_token})
    assert second.status_code == 200
    latest = TokenPair.model_validate(second.json())
    assert (await client.get("/api/v1/me", headers=bearer(latest))).status_code == 200
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": original.refresh_token})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "auth.session_expired"
    assert (await client.get("/api/v1/me", headers=bearer(latest))).status_code == 401
    assert (await client.get("/api/v1/me", headers=bearer(different_device))).status_code == 200


async def test_concurrent_refresh_replay_revokes_the_winning_pair(
    account: User, client: httpx.AsyncClient
) -> None:
    """FR-AUTH-3: concurrent refresh requests admit one rotation and revoke that family on reuse."""
    original = await new_device(account)
    payload = {"refresh_token": original.refresh_token}
    responses = await asyncio.gather(
        client.post("/api/v1/auth/refresh", json=payload), client.post("/api/v1/auth/refresh", json=payload)
    )
    assert sorted(response.status_code for response in responses) == [200, 401]
    winner = TokenPair.model_validate(
        next(response for response in responses if response.status_code == 200).json()
    )
    assert (await client.get("/api/v1/me", headers=bearer(winner))).status_code == 401


async def test_logout_revokes_one_device_and_logout_all_revokes_remaining_devices(
    account: User, client: httpx.AsyncClient
) -> None:
    """FR-AUTH-3: revocations are enforced immediately on signed-in routes."""
    first = await new_device(account)
    second = await new_device(account)
    third = await new_device(account)
    logout = await client.post("/api/v1/auth/logout", headers=bearer(first))
    assert (logout.status_code, logout.content) == (204, b"")
    assert (await client.get("/api/v1/me", headers=bearer(first))).status_code == 401
    assert (await client.get("/api/v1/me", headers=bearer(second))).status_code == 200
    logout_all = await client.post("/api/v1/auth/logout-all", headers=bearer(second))
    assert (logout_all.status_code, logout_all.content) == (204, b"")
    assert (await client.get("/api/v1/me", headers=bearer(second))).status_code == 401
    assert (await client.get("/api/v1/me", headers=bearer(third))).status_code == 401


async def test_refresh_extends_idle_expiry_only_up_to_absolute_expiry(
    account: User, client: httpx.AsyncClient
) -> None:
    """FR-AUTH-3: a refresh cannot extend the session beyond its absolute deadline."""
    pair = await new_device(account)
    now = datetime.now(UTC)
    absolute = now + timedelta(days=2)
    async with AsyncSessionLocal.begin() as db:
        await db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == account.id)
            .values(expires_at=now + timedelta(days=1), absolute_expires_at=absolute)
        )
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": pair.refresh_token})
    ).status_code == 200
    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(AuthSession).where(AuthSession.user_id == account.id))
        assert row is not None and row.expires_at == row.absolute_expires_at == absolute


async def test_suspended_user_can_read_me_refresh_and_logout_but_not_logout_all(
    account: User, client: httpx.AsyncClient
) -> None:
    """FR-AUTH-3: suspended sessions only use paths permitted by the appeals allowlist."""
    pair = await new_device(account)
    async with AsyncSessionLocal.begin() as db:
        await db.execute(update(User).where(User.id == account.id).values(status="suspended"))
    response = await client.get("/api/v1/me", headers=bearer(pair))
    assert response.status_code == 200 and response.json()["status"] == "suspended"
    blocked = await client.post("/api/v1/auth/logout-all", headers=bearer(pair))
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "account.suspended"
    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": pair.refresh_token})
    assert rotated.status_code == 200
    response = await client.post(
        "/api/v1/auth/logout", headers=bearer(TokenPair.model_validate(rotated.json()))
    )
    assert response.status_code == 204


@pytest.mark.parametrize("deadline", ["expires_at", "absolute_expires_at"])
async def test_expired_sessions_cannot_refresh_or_use_access_tokens(
    account: User, client: httpx.AsyncClient, deadline: str
) -> None:
    """FR-AUTH-3: both session deadlines are enforced independently of JWT expiry."""
    pair = await new_device(account)
    async with AsyncSessionLocal.begin() as db:
        await db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == account.id)
            .values({deadline: datetime.now(UTC) - timedelta(seconds=1)})
        )
    assert (await client.get("/api/v1/me", headers=bearer(pair))).status_code == 401
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": pair.refresh_token})
    assert response.status_code == 401 and response.json()["error"]["code"] == "auth.session_expired"


async def test_banned_users_cannot_refresh_or_use_access_tokens(
    account: User, client: httpx.AsyncClient
) -> None:
    """FR-AUTH-3: a ban is enforced even if the user already holds valid tokens."""
    pair = await new_device(account)
    async with AsyncSessionLocal.begin() as db:
        await db.execute(update(User).where(User.id == account.id).values(status="banned"))
    assert (await client.get("/api/v1/me", headers=bearer(pair))).status_code == 401
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": pair.refresh_token})
    ).status_code == 401


async def test_unknown_refresh_token_uses_the_session_expired_error(client: httpx.AsyncClient) -> None:
    """FR-AUTH-3: an unknown token gets the same response as an expired token."""
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": uuid4().hex})
    assert response.status_code == 401 and response.json()["error"]["code"] == "auth.session_expired"
