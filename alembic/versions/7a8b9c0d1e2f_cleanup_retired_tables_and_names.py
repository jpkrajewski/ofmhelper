"""Database cleanup (2026-11-03, checked against prod):

- the empty tables of removed features go: todos, approval_tokens, models,
  instagram_accounts, instagram_stats, model_contacts, competitor_profiles.
  drive_day_folders stays for now: it holds 16 rows, and a downgrade must give
  back every row (tests/integration/migrations/test_prod_snapshot.py);
- tables named for what they hold, next to their owner: profile_groups ->
  watched_profile_groups, profile_scan_stats -> watched_profile_scans,
  account_snapshots -> tracked_account_snapshots, short_link_days ->
  short_link_daily_stats, link_syncs -> short_link_syncs;
- a Higgsfield key pasted into the download box (prod, 2026-11-02) masked in the
  job that stored it;
- `jobs.preview` cleared on finished jobs: it is only read while a job runs.

Downgrade restores the names and recreates the retired tables empty. The masked
key and the cleared previews do not come back.

Revision ID: 7a8b9c0d1e2f
Revises: 4e5f6a7b8c9d
Create Date: 2026-11-03 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7a8b9c0d1e2f"
down_revision: str | Sequence[str] | None = "4e5f6a7b8c9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RENAMED = (
    ("profile_groups", "watched_profile_groups"),
    ("profile_scan_stats", "watched_profile_scans"),
    ("account_snapshots", "tracked_account_snapshots"),
    ("short_link_days", "short_link_daily_stats"),
    ("link_syncs", "short_link_syncs"),
)
# Dependents first.
_RETIRED = (
    "instagram_stats",
    "instagram_accounts",
    "model_contacts",
    "competitor_profiles",
    "models",
    "approval_tokens",
    "todos",
)
_HIGGSFIELD_KEY = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{32,}"
)


def upgrade() -> None:
    for table in _RETIRED:
        op.drop_table(table)
    for old, new in _RENAMED:
        op.rename_table(old, new)
    op.execute(
        sa.text(
            "UPDATE jobs SET"
            " params = regexp_replace(params::text, :key, '[REDACTED]', 'g')::jsonb,"
            " result = regexp_replace(result::text, :key, '[REDACTED]', 'g')::jsonb"
            " WHERE params::text ~ :key OR result::text ~ :key"
        ).bindparams(key=_HIGGSFIELD_KEY)
    )
    op.execute("UPDATE jobs SET preview = NULL WHERE status <> 'running'")


def downgrade() -> None:
    for old, new in _RENAMED:
        op.rename_table(new, old)
    op.create_table(
        "todos",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("model_name", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("comments", sa.Text()),
        sa.Column("checked", sa.Boolean()),
        sa.Column("created_at", sa.Float(precision=53)),
        sa.Column("created_by", sa.String()),
        sa.Column("asset_path", sa.Text()),
        sa.Column("asset_name", sa.Text()),
        sa.Column("approved", sa.Boolean()),
        sa.Column("rejected", sa.Boolean()),
        sa.Column("reject_comment", sa.Text()),
        sa.Column("drive_file_id", sa.String()),
        sa.Column("drive_uploaded_at", sa.Float(precision=53)),
        sa.Column("drive_upload_job_id", sa.String()),
    )
    op.create_table(
        "approval_tokens",
        sa.Column("token", sa.String(), primary_key=True),
        sa.Column("todo_id", sa.String()),
        sa.Column("asset_path", sa.Text()),
        sa.Column("created_at", sa.Float(precision=53)),
        sa.Column("expires_at", sa.Float(precision=53)),
        sa.Column("used_at", sa.Float(precision=53)),
    )
    op.create_table(
        "models",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("profile_picture_path", sa.Text()),
        sa.Column("profile_picture_name", sa.Text()),
        sa.Column("onlyfans_url", sa.Text()),
        sa.Column("created_at", sa.Float(precision=53)),
    )
    op.create_table(
        "instagram_accounts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "model_id", sa.String(), sa.ForeignKey("models.id", ondelete="CASCADE")
        ),
        sa.Column("url", sa.Text()),
        sa.Column("created_at", sa.Float(precision=53)),
        sa.Column("owner", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column("sim_number", sa.Text()),
        sa.Column("password", sa.Text()),
        sa.Column("email", sa.Text()),
    )
    op.create_table(
        "instagram_stats",
        sa.Column(
            "account_id",
            sa.String(),
            sa.ForeignKey("instagram_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("followers", sa.Integer()),
        sa.Column("posts", postgresql.JSONB()),
        sa.Column("checked_at", sa.Float(precision=53)),
        sa.Column("error", sa.Text()),
    )
    op.create_table(
        "model_contacts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "model_id", sa.String(), sa.ForeignKey("models.id", ondelete="CASCADE")
        ),
        sa.Column("type", sa.Text()),
        sa.Column("value", sa.Text()),
        sa.Column("created_at", sa.Float(precision=53)),
    )
    op.create_table(
        "competitor_profiles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "model_id", sa.String(), sa.ForeignKey("models.id", ondelete="CASCADE")
        ),
        sa.Column("url", sa.Text()),
        sa.Column("created_at", sa.Float(precision=53)),
    )
    op.create_index("ix_todos_created_at", "todos", ["created_at"])
    op.create_index("ix_approval_tokens_expires_at", "approval_tokens", ["expires_at"])
    op.create_index("ix_models_created_at", "models", ["created_at"])
    op.create_index(
        "ix_instagram_accounts_model_id", "instagram_accounts", ["model_id"]
    )
    op.create_index("ix_model_contacts_model_id", "model_contacts", ["model_id"])
    op.create_index(
        "ix_competitor_profiles_model_id", "competitor_profiles", ["model_id"]
    )
