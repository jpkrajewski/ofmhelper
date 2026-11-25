# Copyright (C) 2026 jpkrajewski

import logging
lazy from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c0"
down_revision: str | None = "c3d4e5f6a7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

_LINK = r"(https?://|www\.|\[/?url|<a\s|\[/?link)"
_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "telegram_handle",
    "instagram_handle",
    "goals",
)


def upgrade() -> None:
    links = " OR ".join(f"{f} ~* :link" for f in _FIELDS)
    dropped = op.get_bind().execute(
        sa.text(
            f"DELETE FROM applications WHERE {links}"  # noqa: S608
            r" OR telegram_handle ~ '\s'",
        ),
        {"link": _LINK},
    )
    logger.info("dropped %d spam application(s)", dropped.rowcount)


def downgrade() -> None: ...
