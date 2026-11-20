from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tracked_accounts", sa.Column("untracked_at", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tracked_accounts", "untracked_at")
