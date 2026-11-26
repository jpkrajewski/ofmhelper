# Copyright (C) 2026 jpkrajewski

import sqlalchemy as sa

from alembic import op

revision = "a6b7c8d9e0f2"
down_revision = "f4b5c6d7e8a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clone_examples",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("job_id", name="uq_clone_examples_job_id"),
    )


def downgrade() -> None:
    op.drop_table("clone_examples")
