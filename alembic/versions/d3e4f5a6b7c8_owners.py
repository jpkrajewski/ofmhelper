"""owners: phones, logins, tracked accounts, creators and the reels watch list
belong to a user

A user sees and manages only their own; an admin sees everything. Every
existing row becomes the password admin's (alembic c2d3e4f5a6b7), so admins
keep seeing all of it and nobody else sees any of it. What was unique for
the whole agency is now unique per owner: two users may each have a
"Phone 01", or track the same handle, without seeing the other's. A user
who still owns rows cannot be deleted (RESTRICT): deactivate them instead,
nothing they made is lost.

Watched profiles: one row per owner and username (the scan of a username
is written to every owner's row, so it is still scanned once).

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-10-22 06:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AGENCY_EMAIL = "admin"
OWNED = (
    "devices",
    "logins",
    "tracked_accounts",
    "creators",
    "watched_profiles",
    "profile_groups",
)


def _owner_column(table: str) -> None:
    """user_id, every existing row the password admin's, then NOT NULL."""
    op.add_column(table, sa.Column("user_id", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            f"UPDATE {table} SET user_id = (SELECT id FROM users WHERE email = :email)"  # noqa: S608 -- table names are the constants above
        ).bindparams(email=AGENCY_EMAIL)
    )
    op.alter_column(table, "user_id", nullable=False)
    op.create_foreign_key(
        f"fk_{table}_user_id",
        table,
        "users",
        ["user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(f"ix_{table}_user_id", table, ["user_id"])


def upgrade() -> None:
    for table in OWNED:
        _owner_column(table)

    op.drop_constraint("devices_label_key", "devices", type_="unique")
    op.create_unique_constraint(
        "uq_devices_owner_label", "devices", ["user_id", "label"]
    )

    op.drop_constraint("uq_logins_identity", "logins", type_="unique")
    op.create_unique_constraint(
        "uq_logins_identity", "logins", ["user_id", "service", "provider", "username"]
    )

    op.drop_constraint("creators_name_key", "creators", type_="unique")
    op.create_unique_constraint(
        "uq_creators_owner_name", "creators", ["user_id", "name"]
    )

    op.drop_constraint(
        "uq_tracked_accounts_platform_handle", "tracked_accounts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_tracked_accounts_platform_handle",
        "tracked_accounts",
        ["user_id", "platform", "handle"],
    )
    op.drop_constraint(
        "uq_tracked_accounts_platform_account", "tracked_accounts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_tracked_accounts_platform_account",
        "tracked_accounts",
        ["user_id", "platform", "platform_account_id"],
    )

    op.drop_constraint("watched_profiles_pkey", "watched_profiles", type_="primary")
    op.create_primary_key(
        "watched_profiles_pkey", "watched_profiles", ["user_id", "username"]
    )
    op.create_index("ix_watched_profiles_username", "watched_profiles", ["username"])

    op.drop_constraint("profile_groups_pkey", "profile_groups", type_="primary")
    op.create_primary_key("profile_groups_pkey", "profile_groups", ["user_id", "name"])


def downgrade() -> None:
    """Back to agency-wide rows. Fails, as it must, when two owners hold the
    same label / login / handle / profile: merge those by hand first."""
    op.drop_constraint("profile_groups_pkey", "profile_groups", type_="primary")
    op.create_primary_key("profile_groups_pkey", "profile_groups", ["name"])

    op.drop_index("ix_watched_profiles_username", table_name="watched_profiles")
    op.drop_constraint("watched_profiles_pkey", "watched_profiles", type_="primary")
    op.create_primary_key("watched_profiles_pkey", "watched_profiles", ["username"])

    op.drop_constraint(
        "uq_tracked_accounts_platform_account", "tracked_accounts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_tracked_accounts_platform_account",
        "tracked_accounts",
        ["platform", "platform_account_id"],
    )
    op.drop_constraint(
        "uq_tracked_accounts_platform_handle", "tracked_accounts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_tracked_accounts_platform_handle",
        "tracked_accounts",
        ["platform", "handle"],
    )

    op.drop_constraint("uq_creators_owner_name", "creators", type_="unique")
    op.create_unique_constraint("creators_name_key", "creators", ["name"])

    op.drop_constraint("uq_logins_identity", "logins", type_="unique")
    op.create_unique_constraint(
        "uq_logins_identity", "logins", ["service", "provider", "username"]
    )

    op.drop_constraint("uq_devices_owner_label", "devices", type_="unique")
    op.create_unique_constraint("devices_label_key", "devices", ["label"])

    for table in reversed(OWNED):
        op.drop_index(f"ix_{table}_user_id", table_name=table)
        op.drop_constraint(f"fk_{table}_user_id", table, type_="foreignkey")
        op.drop_column(table, "user_id")
