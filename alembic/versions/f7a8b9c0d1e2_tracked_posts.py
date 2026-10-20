"""tracked posts: a row per post, kept across scans within retention

A tracked account's posts move from the `tracked_accounts.posts` JSONB
(overwritten by every scan) to `tracked_posts`, upserted by the platform's
post id and pruned by age only. `covered_since` replaces the view windows
stored in `metrics` (now derived on read): an account whose stored windows
were all covered had every post read (0), else it is complete from its
oldest stored post. The `posts` column is dropped once copied.

Revision ID: f7a8b9c0d1e2
Revises: e1f2a3b4c5d6
Create Date: 2026-10-20 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tracked_posts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("thumbnail_url", sa.Text(), nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=False),
        sa.Column("posted_at", sa.Float(), nullable=False),
        sa.Column("first_seen_at", sa.Float(), nullable=False),
        sa.Column("last_seen_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["tracked_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "post_id", name="uq_tracked_posts_account_post"
        ),
    )
    op.create_index(
        "ix_tracked_posts_account_id_posted_at",
        "tracked_posts",
        ["account_id", "posted_at"],
    )
    op.add_column(
        "tracked_accounts", sa.Column("covered_since", sa.Float(), nullable=True)
    )
    op.execute(
        """
        INSERT INTO tracked_posts (account_id, post_id, url, thumbnail_url,
            views, posted_at, first_seen_at, last_seen_at)
        SELECT a.id, p ->> 'id', p ->> 'url', COALESCE(p ->> 'thumbnail_url', ''),
            (p ->> 'views')::bigint, (p ->> 'posted_at')::double precision,
            a.scanned_at, a.scanned_at
        FROM tracked_accounts AS a, jsonb_array_elements(a.posts) AS p
        WHERE a.posts IS NOT NULL AND a.scanned_at IS NOT NULL
        ON CONFLICT ON CONSTRAINT uq_tracked_posts_account_post DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE tracked_accounts AS a
        SET covered_since = CASE
            WHEN NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(a.metrics -> 'windows') AS w
                WHERE NOT (w ->> 'covered')::boolean
            ) THEN 0
            ELSE (SELECT min(p.posted_at) FROM tracked_posts AS p
                  WHERE p.account_id = a.id)
        END,
        metrics = a.metrics - 'windows'
        WHERE a.metrics IS NOT NULL
        """
    )
    op.drop_column("tracked_accounts", "posts")


def downgrade() -> None:
    # The old code reads every post back from the JSONB; its view windows
    # (stripped above) fill again on each account's next scan.
    op.add_column(
        "tracked_accounts",
        sa.Column("posts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(
        """
        UPDATE tracked_accounts AS a
        SET posts = (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'id', p.post_id, 'url', p.url, 'views', p.views,
                    'posted_at', p.posted_at, 'thumbnail_url', p.thumbnail_url
                )
                ORDER BY p.posted_at DESC
            )
            FROM tracked_posts AS p
            WHERE p.account_id = a.id
        )
        WHERE a.metrics IS NOT NULL
        """
    )
    op.drop_column("tracked_accounts", "covered_since")
    op.drop_index("ix_tracked_posts_account_id_posted_at", table_name="tracked_posts")
    op.drop_table("tracked_posts")
