"""/drive: one POSTED folder per account instead of a folder per day

`drive_account_folders.posted_folder_id`: the POSTED folder inside the
account folder. Existing rows get NULL, so the first sweep after the
deploy finds (by name, case-insensitively) or makes one in each.

`drive_posted_files`: every file the sweep has seen in a POSTED folder, from
when it first saw it -- Drive keeps no time a file was moved into a folder.

`drive_day_folders` is left as it is: no longer read or written, kept (see
RETIRED_TABLES in alembic/env.py) so a rollback to the previous code finds
its rows. The day folders in Drive are never touched either.

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-10-21 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a5b6c7d8e9f0"
down_revision: str | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drive_account_folders",
        sa.Column("posted_folder_id", sa.String(length=128), nullable=True),
    )
    op.create_table(
        "drive_posted_files",
        sa.Column("file_id", sa.String(length=128), primary_key=True),
        sa.Column(
            "login_id",
            sa.Integer(),
            sa.ForeignKey("logins.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("first_seen_at", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_drive_posted_files_login_seen",
        "drive_posted_files",
        ["login_id", "first_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_drive_posted_files_login_seen", table_name="drive_posted_files")
    op.drop_table("drive_posted_files")
    op.drop_column("drive_account_folders", "posted_folder_id")
