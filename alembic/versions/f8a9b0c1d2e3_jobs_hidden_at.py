"""hidden jobs: a gallery card its owner hid

A card's ⋯ menu hides it from its gallery (/generate, /clone, /clone-long,
/download-assets); "Show hidden" above the gallery brings it back into view,
Unhide for good. A view choice only: the job, its files and the action log
are untouched. `hidden_at` is when it was hidden; NULL = shown. Nothing
existing is hidden.

Revision ID: f8a9b0c1d2e3
Revises: e5f6a7b8c9d1
Create Date: 2026-10-24 01:30:00.000000

"""

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
    # Hidden cards show again: no job is lost.
    op.drop_column("jobs", "hidden_at")
