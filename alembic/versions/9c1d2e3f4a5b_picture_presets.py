"""picture_presets: /pictures presets -- a name, a prompt and up to 10
reference images (asset-store paths, in order) that every run of the preset
sends to Nano Banana Pro with the picture to recreate. Owned (user_id,
ON DELETE RESTRICT: a user with presets is deactivated, not deleted); names
unique per owner.

New table: nothing existing changes.

Revision ID: 9c1d2e3f4a5b
Revises: a2b3c4d5e6f7
Create Date: 2026-10-31 23:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9c1d2e3f4a5b"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "picture_presets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("images", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_picture_presets_owner_name"),
    )


def downgrade() -> None:
    op.drop_table("picture_presets")
