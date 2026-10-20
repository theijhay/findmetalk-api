"""Write safety audit evidence inside the caller's transaction (TR-7)."""

from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.core.actor import Actor
from api.v1.models.admin.audit import AuditLog
from api.v1.schemas.admin.audit import AuditAction


async def record(
    session: AsyncSession,
    *,
    actor: Actor,
    action: str,
    target_type: str,
    target_id: UUID | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if target_id is None and target_type != "config":
        raise ValueError("audit.target_required")
    entry = AuditLog(
        actor_type=actor.type,
        actor_id=actor.id,
        action=AuditAction(action).value,
        target_type=target_type,
        target_id=target_id,
        details=deepcopy(metadata) if metadata is not None else {},
    )
    session.add(entry)
    # Flush detects audit failures before the caller can finish its change;
    # commit and rollback remain the caller's responsibility.
    await session.flush()
