# Copyright (C) 2026 jpkrajewski

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e5f6a7b8c9d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("hidden_at", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "hidden_at")
