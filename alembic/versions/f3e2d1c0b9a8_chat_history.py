"""chat_threads, chat_fan_messages: the history /chatting shows

Each chat's read position, and every fan message with when the model answered
it, kept by the chat history sweep. New tables only; nothing moves.

Revision ID: f3e2d1c0b9a8
Revises: 7a8b9c0d1e2f
Create Date: 2026-11-05 00:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3e2d1c0b9a8"
down_revision: str | Sequence[str] | None = "7a8b9c0d1e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_threads",
        sa.Column("platform", sa.String(length=32), primary_key=True),
        sa.Column("channel_id", sa.String(length=255), primary_key=True),
        sa.Column("model_username", sa.String(length=255), nullable=False),
        sa.Column("last_message_id", sa.String(length=64), nullable=False),
    )
    op.create_table(
        "chat_fan_messages",
        sa.Column("platform", sa.String(length=32), primary_key=True),
        sa.Column("message_id", sa.String(length=64), primary_key=True),
        sa.Column("channel_id", sa.String(length=255), nullable=False),
        sa.Column("model_username", sa.String(length=255), nullable=False),
        sa.Column("fan_name", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.Float(), nullable=False),
        sa.Column("replied_at", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_chat_fan_messages_model_sent",
        "chat_fan_messages",
        ["platform", "model_username", "sent_at"],
    )
    op.create_index(
        "ix_chat_fan_messages_channel",
        "chat_fan_messages",
        ["platform", "channel_id"],
    )


def downgrade() -> None:
    op.drop_table("chat_fan_messages")
    op.drop_table("chat_threads")
