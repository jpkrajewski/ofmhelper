"""drive_va_folders: each VA's own folder in their owner's Drive.

Owner 2026-11-15: "there should be automatic folder for VA created when
there is a new VA, and show in /drive just their name ... whole idea is
cutting VAs from direct google drive access". A VA uploads handmade reels
through the app (/handmade) and they land in this folder; the VA never opens
Drive.

`drive_va_folders`, one row per VA (the same shape as
`drive_account_folders`, which is keyed by an Instagram login and cannot
hold a person):
- `user_id` (PK, FK users.id, ON DELETE CASCADE): the VA. A VA deleted
  drops the row; the Drive folder is never deleted.
- `folder_id` text: the folder's Drive id, kept so a renamed VA renames
  their folder instead of starting another.
- `name` text: the name it was given (the VA's name, `User.label`).
- `root_folder_id` text: the owner's "ALL ACCOUNTS" it lives in. A row of
  another root (the VA moved to another owner, or prod's root in a dev copy
  of the database) is ignored and the folder made again there.
- `updated_at` timestamptz.

Written by the Drive sweep (`features/drive/va_folders.py`, worker and
sweeps); read by /drive (the VA cards) and /handmade (where a reel goes).
Lookup by the key only; an owner's VAs come from `users.owner_id`
(indexed). No data to move: the first sweep after the deploy makes every
VA's folder.

Revision ID: e0f1a2b3c4d6
Revises: d9e0f1a2b3c5
"""

import sqlalchemy as sa

from alembic import op

revision = "e0f1a2b3c4d6"
down_revision = "d9e0f1a2b3c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drive_va_folders",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("folder_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("root_folder_id", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("drive_va_folders")
