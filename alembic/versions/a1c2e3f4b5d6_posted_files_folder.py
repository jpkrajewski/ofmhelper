# Copyright (C) 2026 jpkrajewski

lazy from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c2e3f4b5d6"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drive_posted_files",
        sa.Column("folder_id", sa.String(length=128), nullable=True),
    )
    op.execute(
        "UPDATE drive_posted_files p SET folder_id = f.folder_id"
        " FROM drive_account_folders f WHERE f.login_id = p.login_id",
    )


def downgrade() -> None:
    op.drop_column("drive_posted_files", "folder_id")
