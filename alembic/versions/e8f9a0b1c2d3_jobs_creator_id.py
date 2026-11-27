# Copyright (C) 2026 jpkrajewski

import sqlalchemy as sa

from alembic import op

revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "creator_id",
            sa.Integer(),
            sa.ForeignKey(
                "creators.id",
                name="fk_jobs_creator_id_creators",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_jobs_creator_id_created_at",
        "jobs",
        ["creator_id", "created_at"],
        postgresql_where=sa.text("creator_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_creator_id_created_at", table_name="jobs")
    op.drop_column("jobs", "creator_id")
