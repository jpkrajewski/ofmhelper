"""tracked accounts: removing one untracks it instead of deleting it

`untracked_at` marks an account no longer scanned or listed. Its profile,
posts, snapshots and notes stay; adding the handle again clears the mark
and resumes the same account with its whole history. Every account tracked
today stays tracked (NULL).

Revision ID: a9b0c1d2e3f4
Revises: f7a8b9c0d1e2
Create Date: 2026-10-20 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tracked_accounts", sa.Column("untracked_at", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    # Nothing is deleted: the old code, which has no untracked state, lists
    # and scans removed accounts again.
    op.drop_column("tracked_accounts", "untracked_at")
