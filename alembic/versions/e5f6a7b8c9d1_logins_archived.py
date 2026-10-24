"""archived logins: an account removed everywhere keeps its password

An Instagram account removed on /accounts, or whose Drive folder was
deleted by hand, leaves every phone and /accounts (2026-10-22: one account
is one thing across /accounts, /phones and Drive). Its login -- password,
recovery codes -- is kept, hidden: `archived_at` set, its phone links
kept, restorable by an admin on /phones. Nothing existing is archived.

Revision ID: e5f6a7b8c9d1
Revises: d4e5f6a7b8c0
Create Date: 2026-10-22 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d1"
down_revision: str | None = "d4e5f6a7b8c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("logins", sa.Column("archived_at", sa.Float(), nullable=True))


def downgrade() -> None:
    # Archived logins come back onto their phones: nothing is lost.
    op.drop_column("logins", "archived_at")
