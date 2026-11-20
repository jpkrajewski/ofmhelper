from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9b8a7f6e5d4"
down_revision: str | Sequence[str] | None = "a9b8c7d6e5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_accounts",
        sa.Column(
            "connected_from", sa.String(length=2048), nullable=False, server_default=""
        ),
    )
    op.create_index(
        "ix_chat_fan_messages_open",
        "chat_fan_messages",
        ["platform", "model_username", "channel_id", "sent_at"],
        postgresql_where=sa.text("replied_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_chat_fan_messages_open", table_name="chat_fan_messages")
    op.drop_column("chat_accounts", "connected_from")
