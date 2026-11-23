# Copyright (C) 2026 jpkrajewski

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9c8b7a6e5f4"
down_revision: str | Sequence[str] | None = "e2d3c4b5a6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_model_messages",
        sa.Column("platform", sa.String(length=32), primary_key=True),
        sa.Column("message_id", sa.String(length=64), primary_key=True),
        sa.Column("channel_id", sa.String(length=255), nullable=False),
        sa.Column("sent_at", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_chat_model_messages_channel_sent",
        "chat_model_messages",
        ["platform", "channel_id", "sent_at"],
    )
    op.create_index("ix_chat_model_messages_sent", "chat_model_messages", ["sent_at"])


def downgrade() -> None:
    op.drop_table("chat_model_messages")
