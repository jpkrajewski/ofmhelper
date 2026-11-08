"""self_link_domains, self_link_clicks: self-hosted bridge pages

Our own link provider "self": the custom domains we serve bridge pages on, and
one row per hit on a bridge page (view / continue-through) with its exact time
and the visitor's Cloudflare geo. The links themselves reuse `short_links` with
provider = "self", so nothing here mirrors that table.

Revision ID: d5e6f7a8b9c0
Revises: e7f8a9b0c1d2
Create Date: 2026-11-08 00:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "self_link_domains",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("added_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("host", name="uq_self_link_domains_host"),
    )
    op.create_table(
        "self_link_clicks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "link_id",
            sa.Integer(),
            sa.ForeignKey("short_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("tz", sa.String(length=64), nullable=True),
        sa.Column("referrer", sa.Text(), nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=True),
        sa.Column("device", sa.String(length=16), nullable=True),
        sa.Column("ua", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_self_link_clicks_link_ts", "self_link_clicks", ["link_id", "ts"]
    )


def downgrade() -> None:
    op.drop_index("ix_self_link_clicks_link_ts", table_name="self_link_clicks")
    op.drop_table("self_link_clicks")
    op.drop_table("self_link_domains")
