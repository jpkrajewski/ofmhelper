import sqlalchemy as sa

from alembic import op

revision = "d7e8f9a0b1c2"
down_revision = "4c2a8e1f7b3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "storage_provider",
            sa.Text(),
            nullable=False,
            server_default="gdrive",
        ),
    )
    op.create_check_constraint(
        "ck_users_storage_provider",
        "users",
        "storage_provider IN ('gdrive', 'dropbox')",
    )
    op.create_table(
        "storage_accounts",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.Text(), primary_key=True),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "connected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "provider IN ('dropbox')", name="ck_storage_accounts_provider"
        ),
    )
    op.execute(
        "CREATE TRIGGER storage_accounts_stamp BEFORE INSERT OR UPDATE "
        "ON storage_accounts FOR EACH ROW EXECUTE FUNCTION stamp_row('')"
    )
    op.execute(
        "CREATE TRIGGER storage_accounts_audit AFTER INSERT OR UPDATE OR DELETE "
        "ON storage_accounts FOR EACH ROW EXECUTE FUNCTION "
        "audit_row('', 'refresh_token', 'user_id,provider', 'user_id')"
    )


def downgrade() -> None:
    op.drop_table("storage_accounts")
    op.drop_constraint("ck_users_storage_provider", "users", type_="check")
    op.drop_column("users", "storage_provider")
