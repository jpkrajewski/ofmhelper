"""tracked social accounts, their snapshots, and creators

Revision ID: e1f2a3b4c5d6
Revises: c9d0e1f2a3b4
Create Date: 2026-10-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "creators",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "tracked_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("handle", sa.String(length=64), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("added_at", sa.Float(), nullable=False),
        sa.Column("scanned_at", sa.Float(), nullable=True),
        sa.Column("claimed_at", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("posts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["creator_id"], ["creators.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform", "handle", name="uq_tracked_accounts_platform_handle"
        ),
    )
    op.create_index(
        "ix_tracked_accounts_creator_id", "tracked_accounts", ["creator_id"]
    )
    op.create_table(
        "account_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("taken_at", sa.Float(), nullable=False),
        sa.Column("followers", sa.BigInteger(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["tracked_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_snapshots_account_id_taken_at",
        "account_snapshots",
        ["account_id", "taken_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_snapshots_account_id_taken_at", table_name="account_snapshots"
    )
    op.drop_table("account_snapshots")
    op.drop_index("ix_tracked_accounts_creator_id", table_name="tracked_accounts")
    op.drop_table("tracked_accounts")
    op.drop_table("creators")
