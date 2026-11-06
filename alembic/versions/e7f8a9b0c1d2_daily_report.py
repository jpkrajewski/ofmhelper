"""chat_sales and report_runs: the daily report's sales and its runs

`chat_sales` keeps every sale a platform announces (until now only the ones over
$50 were kept, in `chat_notifications`, for the pop-up); those are copied in, so
the report has the history there is. `report_runs` is one row per report per
day: the claim, where the next window starts, and the counters it saw.

Revision ID: e7f8a9b0c1d2
Revises: c9b8a7f6e5d4
Create Date: 2026-11-06 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | Sequence[str] | None = "c9b8a7f6e5d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_sales",
        sa.Column("platform", sa.String(length=32), primary_key=True),
        sa.Column("event_id", sa.String(length=255), primary_key=True),
        sa.Column("model_username", sa.String(length=255), nullable=False),
        sa.Column("gross_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("at", sa.Float(), nullable=False),
    )
    op.create_index("ix_chat_sales_at", "chat_sales", ["at"])
    op.execute(
        """
        INSERT INTO chat_sales
            (platform, event_id, model_username, gross_cents, currency, source, at)
        SELECT platform, event_id, model_username, amount_cents,
               coalesce(currency, 'USD'), detail, at
        FROM chat_notifications
        WHERE kind = 'sale' AND amount_cents IS NOT NULL
        """
    )
    op.create_table(
        "report_runs",
        sa.Column("name", sa.String(length=64), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("ran_at", sa.Float(), nullable=False),
        sa.Column("sent", sa.Boolean(), nullable=False),
        sa.Column("marks", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("report_runs")
    op.drop_table("chat_sales")
