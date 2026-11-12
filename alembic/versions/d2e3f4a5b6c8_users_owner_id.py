"""users.owner_id: a VA's owner.

A VA (role "va") works for one user: runs on that user's API keys, saves
into that user's asset store, and that user sees the VA's jobs. NULL for
every other role and for a VA nobody owns yet. Written by /users, read on
every request through the cached user row (`features/users/viewing.py`).

Revision ID: d2e3f4a5b6c8
Revises: c1d2e3f4a5b7
"""

import sqlalchemy as sa

from alembic import op

revision = "d2e3f4a5b6c8"
down_revision = "c1d2e3f4a5b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "users_owner_id_fkey",
        "users",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_users_owner_id", "users", ["owner_id"])


def downgrade() -> None:
    # A VA cannot sign in to code that does not know the role: back to user.
    op.execute("UPDATE users SET role = 'user' WHERE role = 'va'")
    op.drop_index("ix_users_owner_id", table_name="users")
    op.drop_constraint("users_owner_id_fkey", "users", type_="foreignkey")
    op.drop_column("users", "owner_id")
