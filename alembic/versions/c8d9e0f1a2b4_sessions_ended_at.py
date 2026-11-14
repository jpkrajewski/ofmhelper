"""users.sessions_ended_at: Logout ends every session of the person.

The session is a signed cookie and nothing on the server knew it: Logout
cleared it in that browser only, and a copy taken before (a stolen cookie,
another device) kept working until it sat idle for 5 h (found 2026-11-14;
owner: sign out everywhere on Logout). There is no session table to delete
from, and none is added: one moment per person is enough.

- `users.sessions_ended_at` timestamptz, NULL = never logged out. Set to
  now by Logout (`db/repositories/users.py` `end_sessions`). A session
  carries when it signed in (`since`, in the cookie); the auth middleware
  refuses one signed in before this moment, on every device. Read with the
  user row the middleware already loads (cached) on every request: no
  extra query.

It sits on `users` because it is a fact about the person's credentials,
like `password_hash`, whose change already ends their other sessions.

Revision ID: c8d9e0f1a2b4
Revises: b7c8d9e0f1a3
"""

import sqlalchemy as sa

from alembic import op

revision = "c8d9e0f1a2b4"
down_revision = "b7c8d9e0f1a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("sessions_ended_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "sessions_ended_at")
