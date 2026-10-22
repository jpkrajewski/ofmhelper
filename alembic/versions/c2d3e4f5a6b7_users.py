"""users, their sign-in identities, password links and API keys

People sign in, not roles.

Until now there were two shared passwords (admin, VA) and a job's `actor`
was the role name. Now each person is a `users` row (Google sign-in; one
password account, email "admin", for when Google is not at hand) and a
job's actor is its user's id. The VA role is gone: its jobs and the admin's
become the password admin's, so nothing is lost and admins see them all.

Revision ID: c2d3e4f5a6b7
Revises: b6c7d8e9f0a1
Create Date: 2026-10-22 04:30:00.000000

"""

import time
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The password account: who `APP_PASSWORD_ADMIN` signs in as.
AGENCY_EMAIL = "admin"
_OLD_ROLES = ("admin", "va")


def upgrade() -> None:
    users = op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("last_login_at", sa.Float(), nullable=True),
        sa.Column("google_refresh_token", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("drive_root_id", sa.String(length=128), nullable=True),
        sa.Column(
            "is_password_account",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "user_identities",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("linked_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("provider", "subject"),
    )
    op.create_index(
        "ix_user_identities_user_id", "user_identities", ["user_id"], unique=False
    )
    op.create_table(
        "password_links",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(
        "ix_password_links_user_id", "password_links", ["user_id"], unique=False
    )
    op.create_table(
        "user_api_keys",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "provider"),
    )
    op.bulk_insert(
        users,
        [
            {
                "email": AGENCY_EMAIL,
                "name": "Admin (password)",
                "role": "admin",
                "active": True,
                "created_at": time.time(),
                "last_login_at": None,
                # EncryptedText stores "" as "": no vault key needed here.
                "google_refresh_token": "",
                "password_hash": "",
                "drive_root_id": None,
                "is_password_account": True,
            }
        ],
    )
    op.execute(
        sa.text(
            "UPDATE jobs SET actor = (SELECT id::text FROM users WHERE email = :email)"
            " WHERE actor IN :roles"
        )
        .bindparams(sa.bindparam("roles", expanding=True))
        .bindparams(email=AGENCY_EMAIL, roles=list(_OLD_ROLES))
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE jobs SET actor = 'admin' WHERE actor IN"
            " (SELECT id::text FROM users)"
        )
    )
    op.drop_table("user_api_keys")
    op.drop_index("ix_password_links_user_id", table_name="password_links")
    op.drop_table("password_links")
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
    op.drop_table("users")
