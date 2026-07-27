"""create instagram_stats

Revision ID: a1b2c3d4e5f6
Revises: c424ff3f1580
Create Date: 2026-07-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "c424ff3f1580"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instagram_stats",
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("followers", sa.Integer(), nullable=True),
        sa.Column("posts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checked_at", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"], ["instagram_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("account_id"),
    )


def downgrade() -> None:
    op.drop_table("instagram_stats")
