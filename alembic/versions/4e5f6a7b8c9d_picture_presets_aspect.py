"""picture_presets.aspect_ratio: a preset's fixed output shape ("3:4");
NULL (every existing preset) keeps following the picture to recreate.

Revision ID: 4e5f6a7b8c9d
Revises: 9c1d2e3f4a5b
Create Date: 2026-11-01 00:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4e5f6a7b8c9d"
down_revision: str | None = "9c1d2e3f4a5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "picture_presets",
        sa.Column("aspect_ratio", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("picture_presets", "aspect_ratio")
