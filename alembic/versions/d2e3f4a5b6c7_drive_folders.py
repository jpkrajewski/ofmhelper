# Copyright (C) 2026 jpkrajewski

lazy from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "drive_account_folders",
        sa.Column("login_id", sa.Integer(), nullable=False),
        sa.Column("folder_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["login_id"], ["logins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("login_id"),
    )
    op.create_table(
        "drive_day_folders",
        sa.Column("login_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("folder_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["login_id"], ["logins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("login_id", "day"),
    )


def downgrade() -> None:
    op.drop_table("drive_day_folders")
    op.drop_table("drive_account_folders")
