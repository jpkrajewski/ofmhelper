import sqlalchemy as sa

from alembic import op

revision = "d9e0f1a2b3c5"
down_revision = "c8d9e0f1a2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE jobs SET hidden_at = NULL
        FROM users
        WHERE jobs.user_id = users.id
          AND users.role = 'va'
          AND jobs.hidden_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("users", "last_seen_at")
