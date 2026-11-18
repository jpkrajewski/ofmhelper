"""One self link per address, whoever owns it

A self link's address is (domain, slug) on a domain this server serves. The
bridge answers it with one link; two owners' links at one address meant the
older one took the other's visitors (security audit 2026-11-18). Owner's
decision the same day: addresses are unique, a new automatic link is
numbered past a taken one (`taken_slugs`), the insert that loses a race is
refused as "already taken".

A partial unique index over the statuses that hold an address (creating,
active, inactive); failed and deleting links do not. Production had no
duplicate on 2026-11-18. Should one exist where this runs, the revision
stops and names it: which link keeps the address is a person's decision.

Revision ID: a7c3e9d41f2b
Revises: 9909cfa699f9
Create Date: 2026-11-18 21:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c3e9d41f2b"
down_revision: str | Sequence[str] | None = "9909cfa699f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HOLDS = "provider = 'self' AND status IN ('creating', 'active', 'inactive')"

# The same condition as `_HOLDS`, written out: a fixed query, no formatting.
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
