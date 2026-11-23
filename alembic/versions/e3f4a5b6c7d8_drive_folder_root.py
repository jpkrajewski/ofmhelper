# Copyright (C) 2026 jpkrajewski

lazy from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: str | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drive_account_folders",
        sa.Column("root_folder_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("drive_account_folders", "root_folder_id")
