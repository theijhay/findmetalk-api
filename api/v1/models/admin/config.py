"""Postgres is the durable source of runtime configuration (FR-CALL-5, REC-8)."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.db.database import Base


class ConfigEntry(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
