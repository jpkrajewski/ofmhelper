"""group label on watched instagram profiles

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-17 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "watched_profiles",
        sa.Column("group_name", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_watched_profiles_group_name", "watched_profiles", ["group_name"]
    )


def downgrade() -> None:
    op.drop_index("ix_watched_profiles_group_name", table_name="watched_profiles")
    op.drop_column("watched_profiles", "group_name")
