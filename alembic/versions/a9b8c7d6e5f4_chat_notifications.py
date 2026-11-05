"""chat_notifications: new subscribers and big sales, for the pop-ups

Kept in the database, not Redis: a sale nobody was online for must still be there
when someone signs in, and a platform repeating an event must not show it twice. New
table only; nothing moves.

Revision ID: a9b8c7d6e5f4
Revises: b7a6c5d4e3f2
Create Date: 2026-11-05 04:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9b8c7d6e5f4"
down_revision: str | Sequence[str] | None = "b7a6c5d4e3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_notifications",
        sa.Column("platform", sa.String(length=32), primary_key=True),
        sa.Column("event_id", sa.String(length=255), primary_key=True),
        sa.Column("model_username", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("detail", sa.String(length=64), nullable=False),
        sa.Column("at", sa.Float(), nullable=False),
    )
    op.create_index("ix_chat_notifications_at", "chat_notifications", ["at"])


def downgrade() -> None:
    op.drop_table("chat_notifications")
