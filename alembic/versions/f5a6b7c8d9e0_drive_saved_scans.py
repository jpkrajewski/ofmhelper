"""drive_account_folders.scan: the last read of each account folder

/drive waited on three sequential Google round trips (~1.7-2.5 s) on every
click. The page now opens at once from the last read (made by the sweep,
Sync and every live load) and the browser fetches a fresh one behind it.
NULL until the folder was read once.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-10-22 07:30:00.000000

"""

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
        "drive_account_folders", sa.Column("scanned_at", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("drive_account_folders", "scanned_at")
    op.drop_column("drive_account_folders", "scan")
