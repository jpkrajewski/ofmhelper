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
