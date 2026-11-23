# Copyright (C) 2026 jpkrajewski

lazy from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clone_ai_checks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("file_index", sa.Integer(), nullable=True),
        sa.Column("video", sa.Text(), nullable=False),
        sa.Column("video_seconds", sa.Float(), nullable=True),
        sa.Column("stills_at", postgresql.JSONB(), nullable=False),
        sa.Column("frame_px", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("glitch_percent", sa.Integer(), nullable=True),
        sa.Column("glitches", postgresql.JSONB(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_system", sa.Text(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("raw_answer", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("sheet_s", sa.Float(), nullable=False),
        sa.Column("llm_s", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_kind", sa.String(length=16), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("thinking_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_clone_ai_checks_job_id", "clone_ai_checks", ["job_id"])
    op.create_table(
        "clone_from_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("file_index", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("check_id", sa.Integer(), nullable=True),
        sa.Column("folder_name", sa.String(length=255), nullable=True),
        sa.Column("drive_file_id", sa.String(length=128), nullable=True),
        sa.Column("drive_url", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("started_at", sa.Float(), nullable=True),
        sa.Column("finished_at", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["check_id"],
            ["clone_ai_checks.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "file_index", name="uq_clone_from_links_file"),
    )


def downgrade() -> None:
    op.drop_table("clone_from_links")
    op.drop_index("ix_clone_ai_checks_job_id", table_name="clone_ai_checks")
    op.drop_table("clone_ai_checks")
