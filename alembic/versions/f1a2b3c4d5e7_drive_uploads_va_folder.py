# Copyright (C) 2026 jpkrajewski

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
        "uq_drive_uploads_file_va",
        "drive_uploads",
        ["job_id", "file_index", "va_id"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM drive_uploads WHERE va_id IS NOT NULL")
    op.drop_constraint("uq_drive_uploads_file_va", "drive_uploads", type_="unique")
    op.drop_constraint("ck_drive_uploads_one_folder", "drive_uploads", type_="check")
    op.drop_column("drive_uploads", "va_id")
    op.alter_column("drive_uploads", "login_id", nullable=False)
