"""drive_uploads: clone results uploaded into an account's Drive folder

One row per result file and account folder: queued, then done (with the
Drive file) or failed (with why). The job, index and login together are
unique, so the same video is never uploaded twice into the same folder.
Deleted with the login (its folder is no longer the app's) or with the
uploader.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-10-22 06:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "drive_uploads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("file_index", sa.Integer(), nullable=False),
        sa.Column("login_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("folder_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("drive_file_id", sa.String(length=128), nullable=True),
        sa.Column("drive_url", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("finished_at", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["login_id"], ["logins.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id", "file_index", "login_id", name="uq_drive_uploads_file_folder"
        ),
    )
    op.create_index("ix_drive_uploads_job_id", "drive_uploads", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_drive_uploads_job_id", table_name="drive_uploads")
    op.drop_table("drive_uploads")
