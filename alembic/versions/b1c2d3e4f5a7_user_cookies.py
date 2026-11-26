# Copyright (C) 2026 jpkrajewski

from pathlib import Path
lazy from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from ofmhelpers.config import settings
from ofmhelpers.db.encrypted import encrypt
from ofmhelpers.log import get_logger

revision: str = "b1c2d3e4f5a7"
down_revision: str | None = "a1c2e3f4b5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = get_logger(__name__)


def _move_shared_file() -> None:
    path = Path(settings.downloaders.cookies_file)
    if not path.is_file():
        logger.info("no shared cookies file at %s: nothing to move", path)
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        logger.info("shared cookies file %s is empty: nothing to move", path)
        return
    conn = op.get_bind()
    admin = conn.execute(
        sa.text("SELECT id FROM users WHERE is_password_account ORDER BY id LIMIT 1"),
    ).scalar()
    if admin is None:
        logger.warning("no password admin yet: shared cookies file %s not moved", path)
        return
    conn.execute(
        sa.text(
            "INSERT INTO user_cookies (user_id, cookies, file_name, uploaded_at)"
            " VALUES (:user_id, :cookies, :file_name, :uploaded_at)"
            " ON CONFLICT (user_id) DO NOTHING",
        ),
        {
            "user_id": admin,
            "cookies": encrypt(text),
            "file_name": path.name,
            "uploaded_at": path.stat().st_mtime,
        },
    )
    logger.info("shared cookies file moved to user %s", admin)


def upgrade() -> None:
    op.create_table(
        "user_cookies",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cookies", sa.Text(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("uploaded_at", sa.Float(), nullable=False),
    )
    _move_shared_file()


def downgrade() -> None:
    op.drop_table("user_cookies")
