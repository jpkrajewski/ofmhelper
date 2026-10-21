"""/drive: each account folder remembers the root folder it is in

`drive_account_folders.root_folder_id`: rows made while
OFM_GDRIVE_ROOT_FOLDER pointed elsewhere (dev, on the live folder) no longer
count for another root -- the sweep finds or makes the folder in the
configured root instead of writing into the old one. Existing rows get
NULL: they match no root, so the next sweep links each login again (by
name, so folders already in the root are adopted, not duplicated).

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-10-21 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: str | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drive_account_folders",
        sa.Column("root_folder_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("drive_account_folders", "root_folder_id")
