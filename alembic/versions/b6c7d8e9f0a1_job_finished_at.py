# Copyright (C) 2026 jpkrajewski

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b6c7d8e9f0a1"
down_revision: str | None = "a5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("finished_at", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "finished_at")
