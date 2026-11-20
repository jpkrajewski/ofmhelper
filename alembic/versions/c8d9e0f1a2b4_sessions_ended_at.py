import sqlalchemy as sa

from alembic import op

revision = "c8d9e0f1a2b4"
down_revision = "b7c8d9e0f1a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("sessions_ended_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "sessions_ended_at")
