"""TR-7, SAFE-9: append-only audit log.

Revision ID: afb80bec9543
Revises: 844544eff684
Create Date: 2026-10-20 16:12:55.581351

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'afb80bec9543'
down_revision: Union[str, None] = '844544eff684'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("target_id IS NOT NULL OR target_type = 'config'", name="ck_audit_log_target_id_required"),
    )
    op.create_index("ix_audit_log_target_created_at", "audit_log", ["target_type", "created_at"])
    op.execute("""
        CREATE FUNCTION reject_audit_log_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log.append_only' USING ERRCODE = '55000';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER audit_log_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_log_mutation()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION reject_audit_log_mutation()")
    op.drop_index("ix_audit_log_target_created_at", table_name="audit_log")
    op.drop_table("audit_log")
