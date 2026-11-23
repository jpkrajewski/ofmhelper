# Copyright (C) 2026 jpkrajewski

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drive_account_folders",
        sa.Column("scan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "drive_account_folders",
        sa.Column("scanned_at", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("drive_account_folders", "scanned_at")
    op.drop_column("drive_account_folders", "scan")
