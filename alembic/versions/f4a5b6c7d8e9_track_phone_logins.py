"""/accounts: every Instagram login already on an active phone is tracked

From now on adding such a login to a phone tracks its account
(`account_tracker.track_login`); this does the same once for the logins
added before. Which services count, and how a username becomes a handle,
come from the app (`tracked_handle`: services.json's `tracked_platform`).
An account already in tracked_accounts -- tracked, or removed by hand
(untracked) -- is left exactly as it is. New rows have never been scanned,
so the tracker scans them on its next sweep.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-10-21 15:00:00.000000

"""

import time
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from ofmhelpers.web.account_tracker import tracked_handle

revision: str = "f4a5b6c7d8e9"
down_revision: str | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    keys = {k for k in (tracked_handle(s, u) for s, u in logins) if k is not None}
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
