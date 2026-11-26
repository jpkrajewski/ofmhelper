# Copyright (C) 2026 jpkrajewski

lazy from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c1d2e3f4a5"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACCOUNT_COLUMNS = ("owner", "phone", "sim_number", "password", "email")


def upgrade() -> None:
    for name in _ACCOUNT_COLUMNS:
        op.add_column("instagram_accounts", sa.Column(name, sa.Text(), nullable=True))
    op.create_table(
        "model_contacts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_contacts_model_id",
        "model_contacts",
        ["model_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_model_contacts_model_id", table_name="model_contacts")
    op.drop_table("model_contacts")
    for name in _ACCOUNT_COLUMNS:
        op.drop_column("instagram_accounts", name)
