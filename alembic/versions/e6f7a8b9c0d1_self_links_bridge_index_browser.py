"""self_link_clicks.browser + a bridge lookup index on short_links

The public bridge looks a link up by (domain, slug) with provider "self" on
every hit; without an index that is a seq scan of every user's links. And the
browser dimension was read from the raw user agent per row -- store it like
platform/device instead.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-11-08 04:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The same precedence the bridge uses at write time (Edge/Opera contain
# "Chrome"; Chrome contains "Safari").
_BACKFILL = """
    UPDATE self_link_clicks SET browser = CASE
        WHEN ua ILIKE '%Edg%' THEN 'Edge'
        WHEN ua ILIKE '%OPR%' OR ua ILIKE '%Opera%' THEN 'Opera'
        WHEN ua ILIKE '%SamsungBrowser%' THEN 'Samsung'
        WHEN ua ILIKE '%Firefox%' OR ua ILIKE '%FxiOS%' THEN 'Firefox'
        WHEN ua ILIKE '%CriOS%' OR ua ILIKE '%Chrome%' THEN 'Chrome'
        WHEN ua ILIKE '%Safari%' THEN 'Safari'
        ELSE 'Other'
    END
    WHERE ua IS NOT NULL
"""


def upgrade() -> None:
    op.add_column(
        "self_link_clicks", sa.Column("browser", sa.String(length=16), nullable=True)
    )
    op.execute(_BACKFILL)
    # Partial index: the bridge only ever looks up active self links.
    op.create_index(
        "ix_short_links_bridge",
        "short_links",
        ["domain", "slug"],
        postgresql_where=sa.text("provider = 'self' AND status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("ix_short_links_bridge", table_name="short_links")
    op.drop_column("self_link_clicks", "browser")
