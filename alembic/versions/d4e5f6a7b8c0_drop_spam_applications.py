"""drop the spam applications bots posted through the landing form

Every lead but one on prod (2026-10-22) was a bot's: BBCode/HTML links in a
field, or a "Telegram handle" of random words with spaces -- shapes no
person types and the form now refuses (routers/apply.py,
`ApplicationForm.spam_reason`). Only rows with one of those two go;
nothing else is touched. Not reversible: the spam is not kept.

Revision ID: d4e5f6a7b8c0
Revises: c3d4e5f6a7b9
Create Date: 2026-10-22 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from ofmhelpers.log import get_logger

revision: str = "d4e5f6a7b8c0"
down_revision: str | None = "c3d4e5f6a7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = get_logger(__name__)

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
            f"DELETE FROM applications WHERE {links}"  # noqa: S608 -- column names are constants
            r" OR telegram_handle ~ '\s'"
        ),
        {"link": _LINK},
    )
    logger.info("dropped %d spam application(s)", dropped.rowcount)


def downgrade() -> None:
    """The spam is gone for good; nothing to put back."""
