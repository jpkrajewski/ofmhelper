"""clone_examples: the examples /clone's SOP shows.

/clone gets an "SOP" button: a fixed handful of examples, each the frame of
a video that was cloned, the result and the prompt that made it, so a new
VA copies a prompt that works. Set once by whoever runs the server
(`python -m ofmhelpers.features.clone.examples add`), not edited on the page
(owner 2026-11-14: "this should not be dynamic").

An example keeps what it shows itself: a person's finished jobs past
`settings.web.max_jobs` are deleted (`JobRepository._enforce_cap`) and a
source video is swept from the asset store, so an example that only pointed
at its run would vanish with it. Hence:

- `id` identity key; also the name of its folder `<uploads>/sop/<id>/`
  (`source.webp`, `result.mp4`, `poster.webp`: copies made when added).
- `title` what the example shows ("Remove tattoos").
- `prompt` the run's prompt, copied.
- `position` the order on the page (then `id`).
- `created_at` timestamptz.
- `job_id` (FK jobs.id, ON DELETE SET NULL, unique): the run it was made
  from, while that run exists: the same run added twice is one example.

Written by `features/clone/examples.py` (the command above), read by
everyone who may open /clone: the one table whose rows are not one
person's. The rows live in the database alone (the repo is public; an
example is a real run).

Revision ID: a6b7c8d9e0f2
Revises: f4b5c6d7e8a1
"""

import sqlalchemy as sa

from alembic import op

revision = "a6b7c8d9e0f2"
down_revision = "f4b5c6d7e8a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clone_examples",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("job_id", name="uq_clone_examples_job_id"),
    )


def downgrade() -> None:
    op.drop_table("clone_examples")
