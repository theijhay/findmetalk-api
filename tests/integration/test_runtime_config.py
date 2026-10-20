"""FR-CALL-5, REC-8: configuration queries, transactions and real Redis expiration."""

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import delete, func, select

from api.core.actor import SYSTEM, Actor
from api.core.redis_client import redis_client
from api.db.database import AsyncSessionLocal, engine
from api.utils.settings import CONFIG_CACHE_TTL_SECONDS
from api.v1.models.admin.config import ConfigEntry
from api.v1.schemas.admin.config import ConfigKey
from api.v1.services.admin.config import get_config, set_config
from tests.test_runtime_config import INTEGER_CASES

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def migrated_config_table() -> None:
    # A fresh local test database must exercise the real revision, rather
    # than create_all concealing differences between migrations and models.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, (stdout + stderr).decode()


@pytest.fixture(autouse=True)
async def clean_config(migrated_config_table: None) -> AsyncIterator[None]:
    async def clean() -> None:
        async with engine.begin() as connection:
            await connection.execute(delete(ConfigEntry))
        await redis_client.delete(*(f"config:{key.value}" for key in ConfigKey))

    await clean()
    try:
        yield
    finally:
        await clean()


@pytest.mark.parametrize(("key", "default", "minimum", "maximum"), INTEGER_CASES)
async def test_missing_numeric_keys_return_the_documented_defaults(
    key: ConfigKey, default: int, minimum: int, maximum: int
) -> None:
    """FR-CALL-5, REC-8: absent rows fall back to the documented values without creating rows."""
    assert await get_config(key) == default
    cached = await redis_client.get(f"config:{key.value}")
    assert cached is not None
    assert json.loads(cached) == default
    assert 0 < await redis_client.ttl(f"config:{key.value}") <= 30
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(ConfigEntry)) == 0


async def test_missing_recording_and_consent_keys_return_the_documented_defaults() -> None:
    """REC-8: missing object and string values keep their documented types and defaults."""
    expected = {"default": {"call": True, "circle": True}, "countries": {}}
    assert await get_config(ConfigKey.RECORDINGS_ENABLED) == expected
    assert await get_config(ConfigKey.RULES_VERSION) == "1"
    assert await get_config(ConfigKey.RECORDING_NOTICE_VERSION) == "1"
    first = await get_config(ConfigKey.RECORDINGS_ENABLED)
    assert isinstance(first, dict)
    first["countries"] = {"NG": {"call": False}}
    assert await get_config(ConfigKey.RECORDINGS_ENABLED) == expected


async def test_set_config_upserts_one_row_and_records_the_actor_and_server_timestamp() -> None:
    """FR-CALL-5: updates persist JSON, UUID attribution and UTC timestamps in the caller's transaction."""
    actor = Actor(type="moderator", id=uuid4())
    key = ConfigKey.RING_TIMEOUT_SECONDS
    async with AsyncSessionLocal.begin() as session:
        await set_config(session, key, 25, actor=actor)
    assert await get_config(key) == 25
    async with AsyncSessionLocal() as session:
        row = await session.get(ConfigEntry, key.value)
        assert row is not None
        assert row.updated_by == actor.id
        assert isinstance(row.updated_at, datetime)
        assert row.updated_at.utcoffset() is not None
        first_updated_at = row.updated_at

    async with AsyncSessionLocal.begin() as session:
        await set_config(session, key, 26, actor=SYSTEM)
    async with AsyncSessionLocal() as session:
        row = await session.get(ConfigEntry, key.value)
        assert row is not None
        assert row.value == 26
        assert row.updated_by is None
        assert row.updated_at >= first_updated_at
        assert await session.scalar(select(func.count()).select_from(ConfigEntry)) == 1


async def test_updated_value_reaches_readers_when_the_real_thirty_second_cache_expires() -> None:
    """FR-CALL-5: committed changes become visible within the real config cache TTL."""
    key = ConfigKey.RING_TIMEOUT_SECONDS
    assert await get_config(key) == 20
    assert 0 < await redis_client.ttl(f"config:{key.value}") <= 30
    async with AsyncSessionLocal.begin() as session:
        await set_config(session, key, 21, actor=SYSTEM)

    await asyncio.sleep(CONFIG_CACHE_TTL_SECONDS)
    assert await get_config(key) == 21
    cached = await redis_client.get(f"config:{key.value}")
    assert cached is not None
    assert json.loads(cached) == 21


@pytest.mark.parametrize("existing", [None, 24])
async def test_rollback_never_publishes_or_persists_a_configuration_change(existing: int | None) -> None:
    """FR-CALL-5, REC-8: the caller can roll back a write without leaking it through the cache."""
    key = ConfigKey.RING_TIMEOUT_SECONDS
    if existing is not None:
        async with AsyncSessionLocal.begin() as session:
            await set_config(session, key, existing, actor=SYSTEM)
    async with AsyncSessionLocal() as session:
        await set_config(session, key, 27, actor=SYSTEM)
        assert await get_config(key) == (existing if existing is not None else 20)
        await session.rollback()
    async with AsyncSessionLocal() as session:
        row = await session.get(ConfigEntry, key.value)
        assert (row.value if row is not None else None) == existing
    assert await get_config(key) == (existing if existing is not None else 20)


async def test_concurrent_first_writes_upsert_without_duplicate_keys() -> None:
    """FR-CALL-5: simultaneous updates to a missing key remain one durable row."""
    key = ConfigKey.RING_TIMEOUT_SECONDS

    async def write(value: int) -> None:
        async with AsyncSessionLocal.begin() as session:
            await set_config(session, key, value, actor=SYSTEM)

    await asyncio.gather(write(22), write(23))
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(ConfigEntry)) == 1
    assert await get_config(key) in (22, 23)


async def test_invalid_write_leaves_the_committed_value_and_attribution_unchanged() -> None:
    """FR-CALL-5: invalid configuration is refused before issuing SQL mutations."""
    key = ConfigKey.RING_TIMEOUT_SECONDS
    actor = Actor(type="user", id=uuid4())
    async with AsyncSessionLocal.begin() as session:
        await set_config(session, key, 25, actor=actor)
    async with AsyncSessionLocal.begin() as session:
        with pytest.raises(ValueError):
            await set_config(session, key, True, actor=SYSTEM)
    async with AsyncSessionLocal() as session:
        row = await session.get(ConfigEntry, key.value)
        assert row is not None
        assert (row.value, row.updated_by) == (25, actor.id)


async def test_recording_switches_round_trip_as_jsonb_with_partial_country_overrides() -> None:
    """REC-8: structured recording switches retain their booleans and partial country overrides."""
    value = {"default": {"call": False, "circle": True}, "countries": {"NG": {"circle": False}}}
    async with AsyncSessionLocal.begin() as session:
        await set_config(session, ConfigKey.RECORDINGS_ENABLED, value, actor=SYSTEM)
    assert await get_config(ConfigKey.RECORDINGS_ENABLED) == value


@pytest.mark.parametrize("cached", ["broken JSON", "true", "500", "null"])
async def test_invalid_cache_falls_back_to_postgres_and_repairs_the_cache(cached: str) -> None:
    """FR-CALL-5: damaged or invalid cached values never override the durable value."""
    key = ConfigKey.RING_TIMEOUT_SECONDS
    async with AsyncSessionLocal.begin() as session:
        await set_config(session, key, 25, actor=SYSTEM)
    await redis_client.set(f"config:{key.value}", cached, ex=30)
    assert await get_config(key) == 25
    repaired = await redis_client.get(f"config:{key.value}")
    assert repaired is not None
    assert json.loads(repaired) == 25


async def test_cache_outage_keeps_postgres_as_the_source_of_truth(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-CALL-5: unavailable Redis does not substitute a default for an existing durable value."""
    key = ConfigKey.RING_TIMEOUT_SECONDS
    async with AsyncSessionLocal.begin() as session:
        await set_config(session, key, 25, actor=SYSTEM)
    monkeypatch.setattr(redis_client, "get", AsyncMock(side_effect=RedisConnectionError()))
    monkeypatch.setattr(redis_client, "set", AsyncMock(side_effect=RedisConnectionError()))
    assert await get_config(key) == 25


async def test_invalid_stored_value_is_refused_instead_of_becoming_a_default() -> None:
    """REC-8: invalid durable configuration fails closed instead of selecting fallback policy."""
    key = ConfigKey.RECORDINGS_ENABLED
    async with AsyncSessionLocal.begin() as session:
        session.add(ConfigEntry(key=key.value, value={"default": {"call": True}}, updated_by=None))
    with pytest.raises(ValueError):
        await get_config(key)
