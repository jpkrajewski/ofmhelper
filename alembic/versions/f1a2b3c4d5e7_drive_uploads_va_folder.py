"""drive_uploads: an upload may go into a VA's own folder.

Owner 2026-11-15: a VA's handmade reels stay on the server and the owner
picks where each goes ("i choose where to upload"); the VA's own folder
(`drive_va_folders`) is one of the choices, next to the account folders.

`drive_uploads` stays the one table of uploads (one concept); its folder is
now either an account's (`login_id`) or a VA's (`va_id`):
- `login_id` becomes nullable.
- `va_id` integer, FK users.id ON DELETE CASCADE, nullable: the VA whose
  own folder it went into (a VA deleted takes the rows, the Drive file
  stays, like a login's).
- CHECK `ck_drive_uploads_one_folder`: exactly one of the two.
- UNIQUE `uq_drive_uploads_file_va` (job_id, file_index, va_id): a file goes
  into a VA folder once, as `uq_drive_uploads_file_folder` does for an
  account's (NULLs never collide).

Written by `features/drive/uploads.py` (queue, the worker); read by the
cards (/clone, /handmade), /drive's picker. No data to move: every row has
a login.

Downgrade deletes the uploads into VA folders (the old schema has no place
for them); the files stay in Drive.

Revision ID: f1a2b3c4d5e7
Revises: e0f1a2b3c4d6
"""

import sqlalchemy as sa

from alembic import op

revision = "f1a2b3c4d5e7"
down_revision = "e0f1a2b3c4d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("drive_uploads", "login_id", nullable=True)
    op.add_column(
        "drive_uploads",
        sa.Column(
            "va_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_drive_uploads_one_folder",
        "drive_uploads",
        "(login_id IS NULL) <> (va_id IS NULL)",
    )
    op.create_unique_constraint(
        "uq_drive_uploads_file_va", "drive_uploads", ["job_id", "file_index", "va_id"]
    )


def downgrade() -> None:
    op.execute("DELETE FROM drive_uploads WHERE va_id IS NOT NULL")
    op.drop_constraint("uq_drive_uploads_file_va", "drive_uploads", type_="unique")
    op.drop_constraint("ck_drive_uploads_one_folder", "drive_uploads", type_="check")
    op.drop_column("drive_uploads", "va_id")
    op.alter_column("drive_uploads", "login_id", nullable=False)
