import sqlalchemy as sa

from alembic import op

revision = "4c2a8e1f7b3d"
down_revision = "6b760327929d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tracked_account_snapshots",
        "followers",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.execute(
        "UPDATE tracked_account_snapshots s SET followers = NULL"
        " FROM tracked_accounts a WHERE a.id = s.account_id"
        " AND a.platform = 'snapchat' AND s.followers = 0"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE tracked_account_snapshots SET followers = 0 WHERE followers IS NULL"
    )
    op.alter_column(
        "tracked_account_snapshots",
        "followers",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
