# Copyright (C) 2026 jpkrajewski

import sqlalchemy as sa

from alembic import op

revision = "b2c3d4e5f6a9"
down_revision = "a1b2c3d4e5f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_event_targets",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sender", sa.Text(), primary_key=True),
        sa.Column("target", sa.Text(), nullable=False),
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
    )
    op.execute(
        "CREATE TRIGGER user_event_targets_stamp BEFORE INSERT OR UPDATE "
        "ON user_event_targets FOR EACH ROW EXECUTE FUNCTION stamp_row('')",
    )
    op.execute(
        "CREATE TRIGGER user_event_targets_audit AFTER INSERT OR UPDATE OR DELETE "
        "ON user_event_targets FOR EACH ROW EXECUTE FUNCTION "
        "audit_row('', 'target', 'user_id,sender', 'user_id')",
    )


def downgrade() -> None:
    op.drop_table("user_event_targets")
