"""clone_ai_checks + clone_from_links: is a generated video too glitchy to post, and
where did it go?

clone_ai_checks: one row per check (ofmhelpers.clone_ai_check): the verdict
(GOOD/GLITCHY, NULL when the check itself failed -- `error`), the model's
glitch percent, glitches and reasoning, and everything needed to debug it:
prompt, raw answer, provider/model, the stills' times, attempts and
timings, tokens used, and the kind of failure (`error_kind`: quota, setup,
failed) with the provider's own text kept apart from the short `error`
(`error_detail`, never shown). Optionally the job result it checked (SET NULL
when that job is deleted). Deleted with its user.

clone_from_links: one row per /clone-links result on its way to Drive: its
check, then the upload into the owner's GOOD REELS or TRIAL REELS folder
(status checking / uploading / done / failed), the result's local `path`
(a cut-off review is queued again from it), `started_at` (when a worker
claimed it; NULL = still queued) and `attempts`. One per result file.
Deleted with its job (CASCADE: a reused job id never finds an old row) and
with its user; its check may go first (SET NULL).

New tables: nothing existing changes.

Revision ID: a2b3c4d5e6f7
Revises: f8a9b0c1d2e3
Create Date: 2026-10-25 02:00:00.000000

"""

from collections.abc import Sequence

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
            ["check_id"], ["clone_ai_checks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "file_index", name="uq_clone_from_links_file"),
    )


def downgrade() -> None:
    # Only the checks' history and where results went; videos, jobs and the
    # Drive files themselves are untouched.
    op.drop_table("clone_from_links")
    op.drop_index("ix_clone_ai_checks_job_id", table_name="clone_ai_checks")
    op.drop_table("clone_ai_checks")
