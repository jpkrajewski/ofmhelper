# Copyright (C) 2026 jpkrajewski

lazy from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "6b760327929d"
down_revision: str | Sequence[str] | None = "a7c3e9d41f2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "key"),
        sa.CheckConstraint(
            "status IN ('started', 'done')",
            name="ck_idempotency_keys_status",
        ),
        sa.CheckConstraint("length(key) <= 255", name="ck_idempotency_keys_key_length"),
    )
    op.create_index(
        "ix_idempotency_keys_created_at",
        "idempotency_keys",
        ["created_at"],
    )
    op.create_index(
        "ix_jobs_running_user_id",
        "jobs",
        ["user_id"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_running_user_id", table_name="jobs")
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
