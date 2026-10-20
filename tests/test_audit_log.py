"""TR-7, SAFE-9: the audit writer validates actions and leaves transaction ownership with callers."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.actor import SYSTEM, Actor
from api.v1.schemas.admin.audit import AuditAction
from api.v1.services.admin.audit import record


@pytest.mark.parametrize("action", list(AuditAction))
async def test_supported_audit_actions_flush_without_committing(action: AuditAction) -> None:
    """TR-7: every documented action uses the caller's session and never commits it."""
    session = AsyncMock(spec=AsyncSession)
    actor = Actor(type="moderator", id=uuid4())
    target_id = uuid4()
    metadata = {"context": {"reason": "test"}}
    await record(
        session, actor=actor, action=action, target_type="user", target_id=target_id, metadata=metadata
    )
    entry = session.add.call_args.args[0]
    assert (entry.actor_type, entry.actor_id, entry.action) == (actor.type, actor.id, action.value)
    assert (entry.target_type, entry.target_id, entry.details) == ("user", target_id, metadata)
    metadata["context"]["reason"] = "changed"
    assert entry.details["context"]["reason"] == "test"
    session.flush.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


async def test_unknown_actions_and_missing_resource_ids_are_refused_before_writing() -> None:
    """SAFE-9: unsupported actions and unidentifiable resource targets never create evidence."""
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError):
        await record(session, actor=SYSTEM, action="unknown.action", target_type="user", target_id=uuid4())
    with pytest.raises(ValueError, match=r"audit\.target_required"):
        await record(session, actor=SYSTEM, action="user.blocked", target_type="user", target_id=None)
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


async def test_config_target_and_system_actor_can_omit_uuid_ids() -> None:
    """TR-7: text-keyed configuration uses metadata attribution and SYSTEM has no user id."""
    session = AsyncMock(spec=AsyncSession)
    await record(session, actor=SYSTEM, action="config.updated", target_type="config", target_id=None)
    entry = session.add.call_args.args[0]
    assert (entry.actor_type, entry.actor_id, entry.target_id, entry.details) == ("system", None, None, {})
