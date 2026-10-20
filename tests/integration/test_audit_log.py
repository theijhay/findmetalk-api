"""TR-7, SAFE-9: Postgres enforces append-only evidence and atomic domain/audit transactions."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.sql import Executable

from api.core.actor import SYSTEM, Actor
from api.db.database import AsyncSessionLocal, engine
from api.v1.models.admin.audit import AuditLog
from api.v1.models.admin.config import ConfigEntry
from api.v1.schemas.admin.config import ConfigKey
from api.v1.services.admin import audit
from api.v1.services.admin.audit import record
from api.v1.services.admin.config import set_config

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("migrated_database")]


@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
async def test_database_refuses_audit_updates_deletes_and_truncation(operation: str) -> None:
    """SAFE-9: direct database mutation fails and leaves existing evidence intact."""
    target_id = uuid4()
    async with AsyncSessionLocal.begin() as session:
        await record(
            session, actor=SYSTEM, action="report.created", target_type="report", target_id=target_id
        )
    statement: Executable
    if operation == "update":
        statement = update(AuditLog).where(AuditLog.target_id == target_id).values(action="report.dismissed")
    elif operation == "delete":
        statement = delete(AuditLog).where(AuditLog.target_id == target_id)
    else:
        statement = text("TRUNCATE audit_log")
    with pytest.raises(DBAPIError, match=r"audit_log\.append_only"):
        async with engine.begin() as connection:
            await connection.execute(statement)
    async with AsyncSessionLocal() as session:
        assert (
            await session.scalar(select(AuditLog.action).where(AuditLog.target_id == target_id))
            == "report.created"
        )


async def test_database_defaults_metadata_and_timestamp_and_requires_resource_uuids() -> None:
    """SAFE-9: server defaults and the config-only null target exception work without the writer."""
    identity, target = uuid4(), uuid4()
    values = {
        "id": identity,
        "actor_type": "system",
        "actor_id": None,
        "action": "user.blocked",
        "target_type": "user",
        "target_id": target,
    }
    async with engine.begin() as connection:
        await connection.execute(insert(AuditLog).values(**values))
    async with AsyncSessionLocal() as session:
        row = await session.get(AuditLog, identity)
        assert row is not None
        assert row.details == {}
        assert row.created_at.utcoffset() is not None
    with pytest.raises(IntegrityError, match="ck_audit_log_target_id_required"):
        async with engine.begin() as connection:
            await connection.execute(insert(AuditLog).values(**{**values, "id": uuid4(), "target_id": None}))


async def test_config_and_audit_roll_back_together_and_uncommitted_evidence_stays_private() -> None:
    """TR-7: a rolled-back configuration change leaves no audit evidence or durable change."""
    key = ConfigKey.RING_TIMEOUT_SECONDS
    actor = Actor(type="moderator", id=uuid4())
    async with AsyncSessionLocal() as session:
        before = await session.scalar(select(ConfigEntry.value).where(ConfigEntry.key == key.value))
    with pytest.raises(RuntimeError, match="rollback"):
        async with AsyncSessionLocal.begin() as session:
            await set_config(session, key, 28, actor=actor)
            assert (
                await session.scalar(
                    select(func.count()).select_from(AuditLog).where(AuditLog.actor_id == actor.id)
                )
                == 1
            )
            async with AsyncSessionLocal() as observer:
                assert (
                    await observer.scalar(
                        select(func.count()).select_from(AuditLog).where(AuditLog.actor_id == actor.id)
                    )
                    == 0
                )
            raise RuntimeError("rollback")
    async with AsyncSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.actor_id == actor.id)
            )
            == 0
        )
        assert await session.scalar(select(ConfigEntry.value).where(ConfigEntry.key == key.value)) == before


async def test_config_audit_records_default_and_stored_old_values_with_the_same_actor() -> None:
    """TR-7: config evidence records each effective from/to value with canonical text-key targeting."""
    key = ConfigKey.RULES_VERSION
    actor = Actor(type="user", id=uuid4())
    async with AsyncSessionLocal.begin() as session:
        await session.execute(delete(ConfigEntry).where(ConfigEntry.key == key.value))
        await set_config(session, key, "2", actor=actor)
        await set_config(session, key, "3", actor=actor)
    async with AsyncSessionLocal() as session:
        rows = list((await session.scalars(select(AuditLog).where(AuditLog.actor_id == actor.id))).all())
    assert len(rows) == 2
    assert all(
        (row.actor_type, row.action, row.target_type, row.target_id)
        == ("user", "config.updated", "config", None)
        for row in rows
    )
    assert {(row.details["key"], row.details["old"], row.details["new"]) for row in rows} == {
        (key.value, "1", "2"),
        (key.value, "2", "3"),
    }


async def test_concurrent_config_writers_record_a_complete_old_new_chain() -> None:
    """TR-7: a transaction advisory lock serializes same-key old/new audit evidence."""
    key = ConfigKey.RING_TIMEOUT_SECONDS
    actor = Actor(type="moderator", id=uuid4())
    async with AsyncSessionLocal.begin() as session:
        await session.execute(delete(ConfigEntry).where(ConfigEntry.key == key.value))

    async def write(value: int) -> None:
        async with AsyncSessionLocal.begin() as session:
            await set_config(session, key, value, actor=actor)

    await asyncio.gather(write(25), write(26))
    async with AsyncSessionLocal() as session:
        rows = list((await session.scalars(select(AuditLog).where(AuditLog.actor_id == actor.id))).all())
        final = await session.scalar(select(ConfigEntry.value).where(ConfigEntry.key == key.value))
    assert len(rows) == 2
    first = next(row for row in rows if row.details["old"] == 20)
    second = next(row for row in rows if row.id != first.id)
    assert second.details["old"] == first.details["new"]
    assert second.details["new"] == final
    assert {row.details["new"] for row in rows} == {25, 26}


async def test_audit_failure_rolls_back_the_configuration_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """TR-7: the caller cannot commit a config change when its audit write fails."""
    key = ConfigKey.RECORDING_NOTICE_VERSION
    async with AsyncSessionLocal() as session:
        before = await session.scalar(select(ConfigEntry.value).where(ConfigEntry.key == key.value))
    monkeypatch.setattr(audit, "record", AsyncMock(side_effect=RuntimeError("audit failed")))
    with pytest.raises(RuntimeError, match="audit failed"):
        async with AsyncSessionLocal.begin() as session:
            await set_config(session, key, "2", actor=SYSTEM)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(ConfigEntry.value).where(ConfigEntry.key == key.value)) == before
