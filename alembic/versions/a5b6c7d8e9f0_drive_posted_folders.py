# Copyright (C) 2026 jpkrajewski

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
