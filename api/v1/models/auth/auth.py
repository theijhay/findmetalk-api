"""Email identities, single-use challenges and issued sessions (FR-AUTH-1, FR-AUTH-4)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from api.db.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'banned')", name="ck_users_status"),
        CheckConstraint("role IN ('user', 'moderator', 'owner')", name="ck_users_role"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    email_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"), nullable=False)
    role: Mapped[str] = mapped_column(Text, server_default=text("'user'"), nullable=False)
    email_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailCode(Base):
    __tablename__ = "email_codes"
    __table_args__ = (CheckConstraint("attempts >= 0", name="ck_email_codes_attempts"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    code_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
