"""chat_accounts remembers its server; chat_fan_messages gets an index on what is open

A Fanvue refresh token works once, and reusing one kills the whole authorisation. Dev
runs on a copy of production's database with the same vault key, so without this a
dev sync would spend production's token: an account now counts only on the server it
was connected on. A row with none counts nowhere; connecting again fills it in.

The pop-ups ask for the open fan messages every 10 s per open admin tab, and the sync
asks again every minute: the partial index keeps that to the few rows still open,
however many answered ones the table holds.

Revision ID: c9b8a7f6e5d4
Revises: a9b8c7d6e5f4
Create Date: 2026-11-05 05:20:00.000000

"""

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
