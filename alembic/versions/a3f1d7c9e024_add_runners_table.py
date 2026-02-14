"""add_runners_table

Revision ID: a3f1d7c9e024
Revises: 6b32cbf8559d
Create Date: 2026-02-14 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f1d7c9e024"
down_revision: Union[str, None] = "6b32cbf8559d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runners",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("runner_id", sa.String(length=255), nullable=False),
        sa.Column("backend", sa.String(length=50), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("proxmox_host", sa.String(length=255), nullable=True),
        sa.Column("proxmox_user", sa.String(length=255), nullable=True),
        sa.Column("proxmox_token_name", sa.String(length=255), nullable=True),
        sa.Column("proxmox_token_value", sa.String(length=255), nullable=True),
        sa.Column("proxmox_node", sa.String(length=255), nullable=True),
        sa.Column("proxmox_vmid", sa.Integer(), nullable=True),
        sa.Column("reset_cmd", sa.Text(), nullable=True),
        sa.Column("cleanup_cmd", sa.Text(), nullable=True),
        sa.Column("readiness_cmd", sa.Text(), nullable=True),
        sa.Column("gitlab_runner_id", sa.Integer(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "backend IN ('proxmox', 'bare_metal')", name="ck_runner_backend"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runners_runner_id", "runners", ["runner_id"], unique=True)
    op.create_index("ix_runners_token", "runners", ["token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_runners_token", table_name="runners")
    op.drop_index("ix_runners_runner_id", table_name="runners")
    op.drop_table("runners")
