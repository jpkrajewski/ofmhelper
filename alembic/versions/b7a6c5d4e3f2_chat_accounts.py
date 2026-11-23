# Copyright (C) 2026 jpkrajewski

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7a6c5d4e3f2"
down_revision: str | Sequence[str] | None = "d9c8b7a6e5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_accounts",
        sa.Column("platform", sa.String(length=32), primary_key=True),
        sa.Column("account_id", sa.String(length=255), primary_key=True),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("is_agency", sa.Boolean(), nullable=False),
        sa.Column("needs_reconnect", sa.Boolean(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_accounts")
