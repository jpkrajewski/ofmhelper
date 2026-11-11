"""job meta: what a job's run cost, kept with the job

A job's provider requests (provider, model, the provider's task id, state,
cost in USD, seconds) were only in Logfire, which keeps 30 days: "what did
this month's clones cost, per provider" could not be asked of the database.
`jobs.meta` (JSONB) holds them as `generations`, with their sum as `cost_usd`,
written when the job is saved. Not `params`: those are a run's inputs, handed
back to its function on a re-run (recovery) and to Recreate.

Existing jobs get `{}`: their cost was never kept.

Revision ID: c1d2e3f4a5b7
Revises: e6f7a8b9c0d1
Create Date: 2026-11-11 11:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c1d2e3f4a5b7"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "meta",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    # The kept costs go; no job is lost.
    op.drop_column("jobs", "meta")
