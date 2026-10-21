"""/drive: each Instagram login's Drive folder and its day folders

`drive_account_folders`: the "<handle> - <owner>" folder of a login in the
shared Drive folder, by Drive id. `drive_day_folders`: one row per login
per day whose folder exists. New tables only: nothing existing changes.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-10-21 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "drive_account_folders",
        sa.Column("login_id", sa.Integer(), nullable=False),
        sa.Column("folder_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["login_id"], ["logins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("login_id"),
    )
    op.create_table(
        "drive_day_folders",
        sa.Column("login_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("folder_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["login_id"], ["logins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("login_id", "day"),
    )


def downgrade() -> None:
    op.drop_table("drive_day_folders")
    op.drop_table("drive_account_folders")
