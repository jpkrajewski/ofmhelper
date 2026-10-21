"""/accounts: every Instagram login already on an active phone is tracked

From now on adding such a login to a phone tracks its account
(`account_tracker.track_login`); this does the same once for the logins
added before. Which services count and how a username becomes a handle
are frozen here as they were at this revision (services.json's
`tracked_platform`, `instagram.models.ProfileHandle`), so replaying it
later does the same whatever the app has become.
An account already in tracked_accounts -- tracked, or removed by hand
(untracked) -- is left exactly as it is. New rows have never been scanned,
so the tracker scans them on its next sweep.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-10-21 15:00:00.000000

"""

import re
import time
from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy as sa

from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: str | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# service -> tracked platform, from services.json at this revision.
_TRACKED = {"instagram": "instagram"}
# ProfileHandle's rule at this revision.
_USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}")


def _tracked_handle(service: str, username: str) -> tuple[str, str] | None:
    platform = _TRACKED.get(service)
    if platform is None:
        return None
    text = username.strip()
    if "instagram.com" in text:
        path = urlsplit(text if "//" in text else f"https://{text}").path
        text = next((part for part in path.split("/") if part), "")
    text = text.lstrip("@").strip("/")
    return (platform, text.lower()) if _USERNAME.fullmatch(text) else None


def upgrade() -> None:
    bind = op.get_bind()
    logins = bind.execute(
        sa.text(
            "SELECT DISTINCT l.service, l.username FROM logins l"
            " JOIN device_logins dl ON dl.login_id = l.id"
            " JOIN devices d ON d.id = dl.device_id"
            " WHERE d.retired_at IS NULL"
        )
    ).all()
    keys = {k for k in (_tracked_handle(s, u) for s, u in logins) if k is not None}
    now = time.time()
    for platform, handle in sorted(keys):
        bind.execute(
            sa.text(
                "INSERT INTO tracked_accounts (platform, handle, notes, added_at)"
                " VALUES (:platform, :handle, '', :now)"
                " ON CONFLICT ON CONSTRAINT uq_tracked_accounts_platform_handle"
                " DO NOTHING"
            ),
            {"platform": platform, "handle": handle, "now": now},
        )


def downgrade() -> None:
    """Nothing to undo: the accounts are real ones the agency runs, and
    once scanned they carry history; removing one is "Remove" on /accounts."""
