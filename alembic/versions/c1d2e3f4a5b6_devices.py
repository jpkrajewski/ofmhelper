"""phones, the logins signed in on them, and which phone has which (/phones)

`devices`: the agency's physical phones (catalog model/color ids
from `ofmhelpers.phones.catalog()`). `logins`: each account once --
`password` and `backup_codes` hold Fernet tokens (web/db/encrypted.py, key
`OFM_VAULT_KEY`) or "" for none, never plaintext. `device_logins`: which
phone a login is signed in on (one login, any number of phones). New tables
only: nothing existing changes.

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-10-21 02:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b0c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=48), nullable=True),
        sa.Column("color_id", sa.String(length=32), nullable=True),
        sa.Column(
            "custom_model", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("owner", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "phone_number", sa.String(length=32), nullable=False, server_default=""
        ),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("added_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("retired_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("label"),
    )
    op.create_table(
        "logins",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("service", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("username", sa.String(length=255), nullable=False),
        # Fernet tokens (EncryptedText), "" for none.
        sa.Column("password", sa.Text(), nullable=False, server_default=""),
        sa.Column("backup_codes", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("added_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service", "provider", "username", name="uq_logins_identity"
        ),
    )
    op.create_table(
        "device_logins",
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("login_id", sa.Integer(), nullable=False),
        sa.Column("linked_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["login_id"], ["logins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id", "login_id"),
    )
    op.create_index("ix_device_logins_login_id", "device_logins", ["login_id"])


def downgrade() -> None:
    op.drop_index("ix_device_logins_login_id", table_name="device_logins")
    op.drop_table("device_logins")
    op.drop_table("logins")
    op.drop_table("devices")
