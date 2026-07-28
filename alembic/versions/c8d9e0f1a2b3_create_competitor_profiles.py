"""competitor instagram profiles per model

Revision ID: c8d9e0f1a2b3
Revises: b7c1d2e3f4a5
Create Date: 2026-07-28 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "b7c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "competitor_profiles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_competitor_profiles_model_id",
        "competitor_profiles",
        ["model_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_competitor_profiles_model_id", table_name="competitor_profiles")
    op.drop_table("competitor_profiles")
