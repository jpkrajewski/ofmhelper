from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2d3c4b5a6f7"
down_revision: str | Sequence[str] | None = "f3e2d1c0b9a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_webhooks",
        sa.Column("platform", sa.String(length=32), primary_key=True),
        sa.Column("model_id", sa.String(length=255), primary_key=True),
        sa.Column("model_username", sa.String(length=255), nullable=False),
        sa.Column("webhook_id", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_webhooks")
