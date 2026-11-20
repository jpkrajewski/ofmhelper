from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c3e9d41f2b"
down_revision: str | Sequence[str] | None = "9909cfa699f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HOLDS = "provider = 'self' AND status IN ('creating', 'active', 'inactive')"

_DUPLICATES = """
    SELECT domain, slug, count(*) AS links
    FROM short_links
    WHERE provider = 'self' AND status IN ('creating', 'active', 'inactive')
    GROUP BY domain, slug
    HAVING count(*) > 1
    ORDER BY domain, slug
"""


def upgrade() -> None:
    duplicates = op.get_bind().execute(sa.text(_DUPLICATES)).all()
    if duplicates:
        listed = ", ".join(f"{d}/{s} ({n} links)" for d, s, n in duplicates)
        msg = (
            f"self link addresses held by more than one link: {listed}. Mark all"
            " but one of each as failed by hand, then run the migration again."
        )
        raise RuntimeError(msg)
    op.create_index(
        "uq_short_links_self_address",
        "short_links",
        ["domain", "slug"],
        unique=True,
        postgresql_where=sa.text(_HOLDS),
    )


def downgrade() -> None:
    op.drop_index("uq_short_links_self_address", table_name="short_links")
