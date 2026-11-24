"""Versioned moderation policies, and the version each submission was checked with.

Version 1 is the example policy of the API specification (README 5.2).
Submissions made before this revision were checked with the same limits, so
they get version 1.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FIRST_POLICY = {
    "max_size_bytes": 52428800,
    "allowed_mime_types": [
        "application/pdf",
        "text/*",
        "image/jpeg",
        "image/png",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
    "blocked_extensions": [".exe", ".msi", ".bat", ".sh", ".apk"],
    "auto_approve": {"groups": ["trusted"], "max_size_bytes": 10485760},
}


def upgrade() -> None:
    policies = op.create_table(
        "policies",
        sa.Column("version", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_policies")),
    )
    op.bulk_insert(policies, [{"version": 1, "document": FIRST_POLICY, "created_by": "watchman"}])

    op.add_column("submissions", sa.Column("policy_version", sa.Integer(), nullable=True))
    op.execute("UPDATE submissions SET policy_version = 1")
    op.alter_column("submissions", "policy_version", nullable=False)
    op.create_foreign_key(
        op.f("fk_submissions_policy_version_policies"),
        "submissions",
        "policies",
        ["policy_version"],
        ["version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_submissions_policy_version_policies"), "submissions", type_="foreignkey"
    )
    op.drop_column("submissions", "policy_version")
    op.drop_table("policies")
