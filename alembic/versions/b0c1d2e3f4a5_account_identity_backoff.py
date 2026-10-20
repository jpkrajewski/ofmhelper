"""tracked accounts: the platform's stable id, and failed-scan backoff

`platform_account_id` (unique per platform; NULL until an account's next
successful scan fills it) lets a renamed account added again under its new
handle merge into the row that holds its history. `failed_scans` counts
consecutive failures, so a banned or deleted account is retried less often.

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-10-20 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
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
        "uq_tracked_accounts_platform_account", "tracked_accounts", type_="unique"
    )
    op.drop_column("tracked_accounts", "failed_scans")
    op.drop_column("tracked_accounts", "platform_account_id")
