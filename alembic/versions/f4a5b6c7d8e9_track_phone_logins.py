# Copyright (C) 2026 jpkrajewski

import re
import time
from urllib.parse import urlsplit
lazy from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: str | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRACKED = {"instagram": "instagram"}
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
            " WHERE d.retired_at IS NULL",
        ),
    ).all()
    keys = {k for k in (_tracked_handle(s, u) for s, u in logins) if k is not None}
    now = time.time()
    for platform, handle in sorted(keys):
        bind.execute(
            sa.text(
                "INSERT INTO tracked_accounts (platform, handle, notes, added_at)"
                " VALUES (:platform, :handle, '', :now)"
                " ON CONFLICT ON CONSTRAINT uq_tracked_accounts_platform_handle"
                " DO NOTHING",
            ),
            {"platform": platform, "handle": handle, "now": now},
        )


def downgrade() -> None: ...
