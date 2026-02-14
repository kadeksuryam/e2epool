"""enrich_operation_log_add_gc_index

Revision ID: 6b32cbf8559d
Revises: 1547a28ef314
Create Date: 2026-02-13 14:20:06.808621

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6b32cbf8559d'
down_revision: Union[str, None] = '1547a28ef314'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # GC partial index for efficient stale checkpoint sweeps
    op.create_index('ix_checkpoints_gc', 'checkpoints', ['created_at'], unique=False, postgresql_where=sa.text("state = 'created'"))

    # Add new columns to operation_logs (nullable first for existing rows)
    op.add_column('operation_logs', sa.Column('runner_id', sa.String(length=255), nullable=True))
    op.add_column('operation_logs', sa.Column('backend', sa.String(length=50), nullable=True))
    op.add_column('operation_logs', sa.Column('result', sa.String(length=50), nullable=True))
    op.add_column('operation_logs', sa.Column('started_at', sa.DateTime(), nullable=True))
    op.add_column('operation_logs', sa.Column('finished_at', sa.DateTime(), nullable=True))
    op.add_column('operation_logs', sa.Column('duration_ms', sa.Integer(), nullable=True))

    # Backfill runner_id from the checkpoints table
    op.execute("""
        UPDATE operation_logs
        SET runner_id = c.runner_id,
            started_at = operation_logs.created_at
        FROM checkpoints c
        WHERE operation_logs.checkpoint_id = c.id
          AND operation_logs.runner_id IS NULL
    """)

    # Now set NOT NULL constraints
    op.alter_column('operation_logs', 'runner_id', nullable=False)
    op.alter_column('operation_logs', 'started_at', nullable=False)

    # Drop old created_at column (replaced by started_at)
    op.drop_column('operation_logs', 'created_at')


def downgrade() -> None:
    op.add_column('operation_logs', sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True))
    op.execute("UPDATE operation_logs SET created_at = started_at")
    op.alter_column('operation_logs', 'created_at', nullable=False)
    op.drop_column('operation_logs', 'duration_ms')
    op.drop_column('operation_logs', 'finished_at')
    op.drop_column('operation_logs', 'started_at')
    op.drop_column('operation_logs', 'result')
    op.drop_column('operation_logs', 'backend')
    op.drop_column('operation_logs', 'runner_id')
    op.drop_index('ix_checkpoints_gc', table_name='checkpoints', postgresql_where=sa.text("state = 'created'"))
