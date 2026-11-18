"""distribution: Pinterest accounts spreading every model's content.

Owner 2026-11-18: "we have so much content we need reliable content
distribution system, pinterest first"; "system just distributes the
content, we just provide accounts"; "links are per model not per account".
Free and automatic: each account connects with its logged-in pinterest.com
cookies (no developer app, no review); every model preset of the owner
(private: the repo is public) adds its Instagram accounts' POSTED reels and
THREADS pictures to every account's queue; pins go out
`OFM_DISTRIBUTION_PINS_PER_DAY` a day per account at jittered times.

`distribution_channels`, one per account and site:
- `id` bigint identity; `user_id` FK users RESTRICT (whose: only they see
  it); `platform` text ("pinterest"); `account_name` text (its name there);
  unique (user_id, platform, account_name): the same cookies again update it.
- `cookies` text, encrypted (`EncryptedText`), masked in `audit_log`;
  `paused_at` timestamptz.
- Written by the sweep, quiet in the stamps: `error` text (why it stopped:
  dead cookies), `next_at` (its next pin, jittered), `resume_at` (rate
  limited until), `cookies` again and `cookies_expire_at` (Pinterest's
  renewed session saved after every pin; the page warns a week before it
  expires).
- Stamps + audit triggers of a1b2c3d4e5f8.

`distribution_presets`, one per owner and model (unique (user_id, key)):
- `id` bigint identity; `user_id` FK users RESTRICT; `key` text (what
  `distribution_posts.preset` names); `data` jsonb: the whole
  `ModelPreset`, read back whole; `link_id` FK short_links SET NULL: the
  model's one self link, made on its first pin. Stamps + audit triggers.

`distribution_posts`, one per channel and Drive file (unique: never twice):
- `id` bigint identity; `channel_id` FK distribution_channels CASCADE;
  `file_id`, `name`, `kind` (image|video), `source` (posted|threads),
  `preset` (the model the file is of), `status`
  (queued|publishing|published|failed), `attempts` int, `job_id`,
  `remote_id` (the pin), `title` / `description` / `link` (what it was
  pinned with: the model's title + a caption + its link, or "ig: <handle>"
  alone), `error`; `created_at`, `started_at`, `published_at` timestamptz.
- Index (channel_id, status): the sweep's and the page's reads; (file_id,
  status): which files the owner's accounts already pinned. Written only by
  the sweep and the publish job: its own record, not audited
  (`db/audit.py` EXCLUDED).

Revision ID: 9909cfa699f9
Revises: b2c3d4e5f6a9
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "9909cfa699f9"
down_revision = "b2c3d4e5f6a9"
branch_labels = None
depends_on = None


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "distribution_channels",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("account_name", sa.Text(), nullable=False),
        sa.Column("cookies", sa.Text(), nullable=False, server_default=""),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resume_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cookies_expire_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.UniqueConstraint(
            "user_id",
            "platform",
            "account_name",
            name="uq_distribution_channels_account",
        ),
    )
    op.create_index(
        "ix_distribution_channels_user_id", "distribution_channels", ["user_id"]
    )
    quiet = "error,next_at,resume_at,cookies,cookies_expire_at"
    op.execute(
        "CREATE TRIGGER distribution_channels_stamp BEFORE INSERT OR UPDATE "
        f"ON distribution_channels FOR EACH ROW EXECUTE FUNCTION stamp_row('{quiet}')"
    )
    op.execute(
        "CREATE TRIGGER distribution_channels_audit AFTER INSERT OR UPDATE OR DELETE "
        "ON distribution_channels FOR EACH ROW EXECUTE FUNCTION "
        f"audit_row('{quiet}', 'cookies', 'id', 'user_id')"
    )
    op.create_table(
        "distribution_presets",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "link_id",
            sa.Integer(),
            sa.ForeignKey("short_links.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_stamps(),
        sa.UniqueConstraint("user_id", "key", name="uq_distribution_presets_owner_key"),
    )
    op.execute(
        "CREATE TRIGGER distribution_presets_stamp BEFORE INSERT OR UPDATE "
        "ON distribution_presets FOR EACH ROW EXECUTE FUNCTION stamp_row('')"
    )
    op.execute(
        "CREATE TRIGGER distribution_presets_audit AFTER INSERT OR UPDATE OR DELETE "
        "ON distribution_presets FOR EACH ROW EXECUTE FUNCTION "
        "audit_row('', '', 'id', 'user_id')"
    )
    op.create_table(
        "distribution_posts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "channel_id",
            sa.BigInteger(),
            sa.ForeignKey("distribution_channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("preset", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("remote_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "channel_id", "file_id", name="uq_distribution_posts_channel_file"
        ),
    )
    op.create_index(
        "ix_distribution_posts_channel_status",
        "distribution_posts",
        ["channel_id", "status"],
    )
    op.create_index(
        "ix_distribution_posts_file_status",
        "distribution_posts",
        ["file_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("distribution_presets")
    op.drop_table("distribution_posts")
    op.drop_table("distribution_channels")
