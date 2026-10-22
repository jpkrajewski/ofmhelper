"""short_links, short_link_days, link_syncs: /links

Links on link providers (Oopsie, Bouncy), their daily views and clicks, and
each user's sync state per provider.

Revision ID: c3d4e5f6a7b9
Revises: b1c2d3e4f5a7
Create Date: 2026-10-22 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3d4e5f6a7b9"
down_revision: str | None = "b1c2d3e4f5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "short_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("remote_id", sa.String(length=300), nullable=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("target", sa.String(length=32), nullable=True),
        sa.Column(
            "creator_id",
            sa.Integer(),
            sa.ForeignKey("creators.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("tracked_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("adult", sa.Boolean(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "provider", "domain", "slug", name="uq_short_links_address"
        ),
        sa.UniqueConstraint(
            "user_id", "provider", "remote_id", name="uq_short_links_remote"
        ),
    )
    op.create_index("ix_short_links_user_id", "short_links", ["user_id"])
    op.create_index("ix_short_links_account_id", "short_links", ["account_id"])
    op.create_table(
        "short_link_days",
        sa.Column(
            "link_id",
            sa.Integer(),
            sa.ForeignKey("short_links.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
    )
    op.create_table(
        "link_syncs",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(length=32), primary_key=True),
        sa.Column("synced_at", sa.Float(), nullable=True),
        sa.Column("claimed_at", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("link_syncs")
    op.drop_table("short_link_days")
    op.drop_index("ix_short_links_account_id", table_name="short_links")
    op.drop_index("ix_short_links_user_id", table_name="short_links")
    op.drop_table("short_links")
