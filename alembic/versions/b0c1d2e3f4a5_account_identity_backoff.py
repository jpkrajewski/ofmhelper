# Copyright (C) 2026 jpkrajewski

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: str | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tracked_accounts",
        sa.Column("platform_account_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tracked_accounts",
        sa.Column("failed_scans", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_unique_constraint(
        "uq_tracked_accounts_platform_account",
        "tracked_accounts",
        ["platform", "platform_account_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_tracked_accounts_platform_account",
        "tracked_accounts",
        type_="unique",
    )
    op.drop_column("tracked_accounts", "failed_scans")
    op.drop_column("tracked_accounts", "platform_account_id")
