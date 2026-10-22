"""drive_posted_files.folder_id: the account folder a posted file was seen in

"Posted today" counted every file ever recorded for a login, whatever
folder it was in. A dev copy of the prod database then showed prod's
posting history on its own, empty "DEV - ALL ACCOUNTS" folders. Now only
files seen in the login's current account folder count. Existing rows get
the folder their login has now (on prod: the one they were seen in).

Revision ID: a1c2e3f4b5d6
Revises: f5a6b7c8d9e0
Create Date: 2026-10-22 08:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c2e3f4b5d6"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drive_posted_files",
        sa.Column("folder_id", sa.String(length=128), nullable=True),
    )
    op.execute(
        "UPDATE drive_posted_files p SET folder_id = f.folder_id"
        " FROM drive_account_folders f WHERE f.login_id = p.login_id"
    )


def downgrade() -> None:
    op.drop_column("drive_posted_files", "folder_id")
