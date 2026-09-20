"""phone + telegram handle on applications

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-17 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default so rows written before this revision get "" rather than a
    # NULL the ORM's str-typed column would choke on; dropped afterwards so the
    # app stays the only thing deciding defaults.
    for column in ("phone", "telegram_handle"):
        op.add_column(
            "applications",
            sa.Column(column, sa.Text(), nullable=False, server_default=""),
        )
        op.alter_column("applications", column, server_default=None)


def downgrade() -> None:
    op.drop_column("applications", "telegram_handle")
    op.drop_column("applications", "phone")
