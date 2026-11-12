"""jobs.user_id replaces jobs.actor; a job's cost is a column.

Whose job it is was `actor`: the user's id as text, no foreign key, no
index, though every gallery, status page and file route filters on it.
`jobs.user_id` (FK users.id, ON DELETE RESTRICT: a user who has jobs is
deactivated, not deleted) takes its place, with `ix_jobs_user_id_created_at`
for "this person's jobs, newest first" and the Spending page's sums. Filled
from `actor` where it is the id of a user who still exists; anything else
(a system event, a removed user's job) is NULL: nobody's. Written by
`db/stores/jobs.py` `create_job` / `log_event`, read by every job read.

What a run cost was `meta.cost_usd` (JSONB): summed per person and period
by /spending, so it is a real column now, `jobs.cost_usd` NUMERIC(12,4),
with `jobs.cost_estimated`: true when the provider reported no cost and the
amount comes from the app's own price data. NULL = no known price, never 0.
`meta.generations` stays; the `cost_usd` key leaves `meta` (one fact, one
place). Written by `features/jobs/runner.py` `_keep_cost`, read by
`features/spending/`.

Revision ID: e3f4a5b6c7d9
Revises: d2e3f4a5b6c8
"""

import sqlalchemy as sa

from alembic import op

revision = "e3f4a5b6c7d9"
down_revision = "d2e3f4a5b6c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("user_id", sa.Integer(), nullable=True))
    # Compared as text: an actor that is no number never reaches a cast.
    op.execute(
        "UPDATE jobs SET user_id = users.id FROM users"
        " WHERE jobs.actor ~ '^[0-9]+$' AND users.id::text = jobs.actor"
    )
    op.create_foreign_key(
        "jobs_user_id_fkey", "jobs", "users", ["user_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_jobs_user_id_created_at", "jobs", ["user_id", "created_at"])
    op.drop_column("jobs", "actor")

    op.add_column("jobs", sa.Column("cost_usd", sa.Numeric(12, 4), nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "cost_estimated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        "UPDATE jobs SET cost_usd = (meta ->> 'cost_usd')::numeric"
        " WHERE meta ->> 'cost_usd' IS NOT NULL"
    )
    op.execute("UPDATE jobs SET meta = meta - 'cost_usd' WHERE meta ? 'cost_usd'")


def downgrade() -> None:
    # A kept meta always had the key (null without a cost); an estimate was
    # never in it.
    op.execute(
        "UPDATE jobs SET meta = meta || jsonb_build_object('cost_usd',"
        " CASE WHEN cost_estimated THEN NULL ELSE cost_usd::double precision END)"
        " WHERE meta <> '{}'::jsonb"
    )
    op.drop_column("jobs", "cost_estimated")
    op.drop_column("jobs", "cost_usd")

    # An actor that was no user's id is not brought back: it became NULL.
    op.add_column("jobs", sa.Column("actor", sa.String(32), nullable=True))
    op.execute("UPDATE jobs SET actor = user_id::text")
    op.drop_index("ix_jobs_user_id_created_at", table_name="jobs")
    op.drop_constraint("jobs_user_id_fkey", "jobs", type_="foreignkey")
    op.drop_column("jobs", "user_id")
