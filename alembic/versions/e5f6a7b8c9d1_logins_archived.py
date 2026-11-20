from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d1"
down_revision: str | None = "d4e5f6a7b8c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("logins", sa.Column("archived_at", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("logins", "archived_at")
