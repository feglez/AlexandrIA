"""Quotas of the members and snapshots of their personal storage.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quotas",
        sa.Column("member", sa.Text(), nullable=False),
        sa.Column("max_contribution_bytes", sa.BigInteger(), nullable=False),
        sa.Column("max_submissions_per_month", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("member", name=op.f("pk_quotas")),
    )
    op.create_table(
        "storage_snapshots",
        sa.Column("member", sa.Text(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("member", "taken_at", name=op.f("pk_storage_snapshots")),
    )


def downgrade() -> None:
    op.drop_table("storage_snapshots")
    op.drop_table("quotas")
