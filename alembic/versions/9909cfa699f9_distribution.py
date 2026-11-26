# Copyright (C) 2026 jpkrajewski

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
        "ix_distribution_channels_user_id",
        "distribution_channels",
        ["user_id"],
    )
    quiet = "error,next_at,resume_at,cookies,cookies_expire_at"
    op.execute(
        "CREATE TRIGGER distribution_channels_stamp BEFORE INSERT OR UPDATE "
        f"ON distribution_channels FOR EACH ROW EXECUTE FUNCTION stamp_row('{quiet}')",
    )
    op.execute(
        "CREATE TRIGGER distribution_channels_audit AFTER INSERT OR UPDATE OR DELETE "
        "ON distribution_channels FOR EACH ROW EXECUTE FUNCTION "
        f"audit_row('{quiet}', 'cookies', 'id', 'user_id')",
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
        "ON distribution_presets FOR EACH ROW EXECUTE FUNCTION stamp_row('')",
    )
    op.execute(
        "CREATE TRIGGER distribution_presets_audit AFTER INSERT OR UPDATE OR DELETE "
        "ON distribution_presets FOR EACH ROW EXECUTE FUNCTION "
        "audit_row('', '', 'id', 'user_id')",
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
            "channel_id",
            "file_id",
            name="uq_distribution_posts_channel_file",
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
