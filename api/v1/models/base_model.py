"""Shared column conventions for every FindMeTalk table."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.database import Base


class BaseTableModel(Base):
    """UUID primary key + created/updated timestamps.

    Timestamps default server-side (``now()``) so rows written outside the ORM
    (migrations, backfills, raw SQL) still get correct values. The Alembic
    revision for a new table must create those server defaults too; CI runs
    ``alembic check`` against real Postgres to catch a model and its
    migration disagreeing.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
