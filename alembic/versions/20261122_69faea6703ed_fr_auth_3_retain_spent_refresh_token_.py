"""FR-AUTH-3 retain spent refresh token hashes

Revision ID: 69faea6703ed
Revises: 601d63290fc3
Create Date: 2026-11-22 21:35:36.137396

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '69faea6703ed'
down_revision: Union[str, None] = '601d63290fc3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "spent_refresh_tokens",
        sa.Column("token_hash", sa.LargeBinary(), primary_key=True),
        sa.Column("session_id", sa.UUID(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_index("ix_spent_refresh_tokens_session_id", "spent_refresh_tokens", ["session_id"])


def downgrade() -> None:
    op.drop_table("spent_refresh_tokens")
