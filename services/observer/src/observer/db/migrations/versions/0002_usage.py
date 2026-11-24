"""Usage ledger and the totals per member and month.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("member", sa.Text(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_usage_events")),
    )
    op.create_table(
        "usage_totals",
        sa.Column("member", sa.Text(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("contributed_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("submissions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("searches", sa.Integer(), server_default="0", nullable=False),
        sa.Column("questions", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("member", "period", name=op.f("pk_usage_totals")),
    )


def downgrade() -> None:
    op.drop_table("usage_totals")
    op.drop_table("usage_events")
