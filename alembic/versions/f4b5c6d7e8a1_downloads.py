"""downloads: one row per downloaded file.

A download was only its job: the files in the job's JSONB `result`. Nothing
could ask "has this person downloaded this link", "which videos are a day
old and unused" or "which was asked for last" without reading every result.
Those are filtered and sorted on, so they are real columns now
(`.claude/rules/database.md`), one row per file:

- `user_id` (FK users.id, ON DELETE RESTRICT: the job's owner) + `url` (the
  pasted link), index `ix_downloads_user_id_url`: the same link asked again
  is found, not downloaded twice.
- `job_id` (FK jobs.id, ON DELETE CASCADE) + `position` (the file's index
  among the job's files, the `{i}` of `/files/{job}/{i}`), unique together.
- `file_path` (where the downloader left it), `asset_path` (its copy in the
  asset store, what a clone names; NULL until looked up).
- `stored` (the video is on this server), `removed_at`; `created_at`,
  `last_used_at`, index `ix_downloads_stored_last_used_at`: the retention
  sweep's read, and /download-assets' order.

Written by `features/downloads/service.py` (a download job finishing, the
same link pasted again) and `features/downloads/cleanup.py` (the sweep);
read by those, `db/stores/jobs.py` (a removed video keeps its card),
`db/repositories/jobs.py` (the gallery's order) and `/refs/missing`. The
job row stays the run's record; its `result` still lists the files.

Filled from the done jobs of the three download tasks: one row per output
file, `stored` true (the sweep's first round finds what is really there),
`last_used_at` = the job's start. `asset_path` cannot be read from the row:
the sweep fills it. A job that is nobody's (`user_id` NULL) gets no rows.

Revision ID: f4b5c6d7e8a1
Revises: e3f4a5b6c7d9
"""

import sqlalchemy as sa

from alembic import op

revision = "f4b5c6d7e8a1"
down_revision = "e3f4a5b6c7d9"
branch_labels = None
depends_on = None

# A result that is not the grouped list (still running, failed, an old
# one-entry-per-file result) has no rows: each CASE keeps a set-returning
# function from ever seeing anything but an array.
_BACKFILL = """
INSERT INTO downloads
    (user_id, job_id, url, position, file_path, stored, created_at, last_used_at)
SELECT j.user_id,
       j.id,
       f.url,
       row_number() OVER (PARTITION BY j.id ORDER BY f.entry_no, f.file_no) - 1,
       f.file_path,
       true,
       to_timestamp(j.created_at),
       to_timestamp(j.created_at)
FROM jobs AS j
CROSS JOIN LATERAL (
    SELECT e.value ->> 'url' AS url,
           e.ordinality AS entry_no,
           p.value AS file_path,
           p.ordinality AS file_no
    FROM jsonb_array_elements(
             CASE WHEN jsonb_typeof(j.result) = 'array' THEN j.result
                  ELSE '[]'::jsonb END
         ) WITH ORDINALITY AS e
    CROSS JOIN LATERAL jsonb_array_elements_text(
             CASE WHEN jsonb_typeof(e.value -> 'output_paths') = 'array'
                  THEN e.value -> 'output_paths'
                  ELSE '[]'::jsonb END
         ) WITH ORDINALITY AS p
    WHERE e.value ->> 'url' IS NOT NULL
) AS f
WHERE j.task IN ('download_media', 'download_videos', 'download_images')
  AND j.status = 'done'
  AND j.user_id IS NOT NULL
"""


def upgrade() -> None:
    op.create_table(
        "downloads",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("asset_path", sa.Text(), nullable=True),
        sa.Column(
            "stored", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "position", name="uq_downloads_job_position"),
    )
    op.create_index("ix_downloads_user_id_url", "downloads", ["user_id", "url"])
    op.create_index(
        "ix_downloads_stored_last_used_at", "downloads", ["stored", "last_used_at"]
    )
    op.execute(_BACKFILL)


def downgrade() -> None:
    # Nothing to give back: every row was read from a job's result, which the
    # job still has.
    op.drop_index("ix_downloads_stored_last_used_at", table_name="downloads")
    op.drop_index("ix_downloads_user_id_url", table_name="downloads")
    op.drop_table("downloads")
