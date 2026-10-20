"""Durable, validated runtime configuration with a bounded cache (FR-CALL-5, REC-8)."""

import json
from copy import deepcopy
from typing import Any

from pydantic import JsonValue
from redis.exceptions import RedisError
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.actor import Actor
from api.core.redis_client import redis_client
from api.db.database import AsyncSessionLocal
from api.utils.logger import logger
from api.v1.models.admin.config import ConfigEntry
from api.v1.schemas.admin.config import (
    CONFIG_CACHE_TTL_SECONDS,
    RUNTIME_CONFIG_DEFAULTS,
    RUNTIME_CONFIG_INTEGER_RANGES,
    ConfigKey,
    RecordingSwitches,
)
from api.v1.services.admin import audit


def validate_config_value(key: ConfigKey, value: Any) -> JsonValue:
    bounds = RUNTIME_CONFIG_INTEGER_RANGES.get(key)
    if bounds is not None:
        if type(value) is not int or not bounds[0] <= value <= bounds[1]:
            raise ValueError("config.value_invalid")
        return value
    if key == ConfigKey.RECORDINGS_ENABLED:
        return RecordingSwitches.model_validate(value, strict=True).model_dump(mode="json")
    if (
        key in {ConfigKey.RULES_VERSION, ConfigKey.RECORDING_NOTICE_VERSION}
        and isinstance(value, str)
        and 1 <= len(value) <= 64
    ):
        return value
    raise ValueError("config.value_invalid")


async def get_config(key: ConfigKey) -> JsonValue:
    key = ConfigKey(key)
    cache_key = f"config:{key.value}"
    try:
        cached = await redis_client.get(cache_key)
        if cached is not None:
            try:
                return validate_config_value(key, json.loads(cached))
            except ValueError:
                # A damaged cache cannot override durable configuration or
                # turn an invalid value into a fallback policy decision.
                logger.warning("invalid config cache", extra={"key": key.value})
    except RedisError:
        logger.warning("config cache unavailable", extra={"operation": "get"})

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(ConfigEntry.value).where(ConfigEntry.key == key.value))
        ).one_or_none()
    value = validate_config_value(key, row[0] if row is not None else deepcopy(RUNTIME_CONFIG_DEFAULTS[key]))
    try:
        await redis_client.set(cache_key, json.dumps(value), ex=CONFIG_CACHE_TTL_SECONDS)
    except RedisError:
        logger.warning("config cache unavailable", extra={"operation": "set"})
    return value


async def set_config(session: AsyncSession, key: ConfigKey, value: Any, *, actor: Actor) -> None:
    key = ConfigKey(key)
    validated = validate_config_value(key, value)
    # A row lock cannot serialize two first writes to a key that is absent.
    # Hold this key's transaction lock through both its change and evidence.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('config:' || :key))"), {"key": key.value}
    )
    previous = (
        await session.execute(select(ConfigEntry.value).where(ConfigEntry.key == key.value))
    ).one_or_none()
    old_value = previous[0] if previous is not None else RUNTIME_CONFIG_DEFAULTS[key]
    statement = insert(ConfigEntry).values(key=key.value, value=validated, updated_by=actor.id)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[ConfigEntry.key],
            set_={"value": validated, "updated_by": actor.id, "updated_at": func.now()},
        )
    )
    await audit.record(
        session,
        actor=actor,
        action="config.updated",
        target_type="config",
        target_id=None,
        metadata={"key": key.value, "old": old_value, "new": validated},
    )
    # The caller owns the commit. Do not publish uncommitted configuration
    # into Redis; readers refresh committed values when their short TTL ends.
