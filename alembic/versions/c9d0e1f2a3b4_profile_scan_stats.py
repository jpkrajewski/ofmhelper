"""per-scan statistics of watched instagram profiles

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-17 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profile_scan_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("group_name", sa.String(length=64), nullable=True),
        sa.Column("scanned_at", sa.Float(), nullable=False),
        sa.Column("window_days", sa.Float(), nullable=False),
        sa.Column("followers", sa.BigInteger(), nullable=False),
        sa.Column("reels", sa.Integer(), nullable=False),
        sa.Column("eligible", sa.Integer(), nullable=False),
        sa.Column("views_total", sa.BigInteger(), nullable=False),
        sa.Column("views_max", sa.BigInteger(), nullable=False),
        sa.Column("views_median", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_profile_scan_stats_username_scanned_at",
        "profile_scan_stats",
        ["username", "scanned_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_profile_scan_stats_username_scanned_at", table_name="profile_scan_stats"
    )
    op.drop_table("profile_scan_stats")
