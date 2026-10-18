"""google drive connections

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-18 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "drive_connections",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("drive_connections")
