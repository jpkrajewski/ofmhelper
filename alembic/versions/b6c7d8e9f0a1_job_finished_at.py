"""jobs.finished_at: when a job reached done/failed

With `created_at` (the submit) it gives how long a run took, which /clone
shows on each card and turns into the next run's estimate. Existing rows
get NULL: their finish time was never recorded, so they carry no duration.

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-10-22 01:00:00.000000

"""

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
