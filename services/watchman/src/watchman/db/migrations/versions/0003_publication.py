"""Publication state of a submission, used by the mover.

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
    op.add_column("submissions", sa.Column("library_path", sa.Text(), nullable=True))
    op.add_column("submissions", sa.Column("file_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "submissions",
        sa.Column("publish_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "submissions",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("submissions", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("submissions", "last_error")
    op.drop_column("submissions", "next_attempt_at")
    op.drop_column("submissions", "publish_attempts")
    op.drop_column("submissions", "file_id")
    op.drop_column("submissions", "library_path")
