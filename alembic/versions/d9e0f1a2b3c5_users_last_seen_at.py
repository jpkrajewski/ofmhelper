"""users.last_seen_at, and every card a VA hid is shown again.

Owner 2026-11-14: "what if VA hides clones from me? dont give them this
posibilty", and "i need clear logic for a day: when VA logged in ... when
did they log Out ... shorter log out when they dont do stuff for 2 hours".

- `users.last_seen_at` timestamptz, NULL = not seen since this column came
  (no backfill: a value copied from `last_login_at` would make the first
  idle sweep write a logout nobody had). The moment of the person's last
  action (a page opened, anything sent; never a script's poll).
  Writers: the auth middleware, at most once a minute per person
  (`features/users/presence.py`), and a sign-in. Readers: the idle sweep
  (`features/users/idle.py`: a VA not seen for 2 h is logged out and the
  logout is recorded at this moment) and /vas ("Active 14:05").
  On `users` because it is one fact about the person, like
  `sessions_ended_at` next to it: the session itself is a cookie, there is
  no session table. No index: `users` is tens of rows, read whole.

- Data: `jobs.hidden_at` is cleared on every job a VA made. A VA could hide
  a card and it left the owner's gallery too (one column). A VA hides
  nothing any more (`auth/access.py` `Action.HIDE`); what they hid
  before comes back.

Downgrade drops the column. It does not hide the cards again: which ones
were hidden is not kept, and the owner asked for them shown.

Revision ID: d9e0f1a2b3c5
Revises: c8d9e0f1a2b4
"""

import sqlalchemy as sa

from alembic import op

revision = "d9e0f1a2b3c5"
down_revision = "c8d9e0f1a2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE jobs SET hidden_at = NULL
        FROM users
        WHERE jobs.user_id = users.id
          AND users.role = 'va'
          AND jobs.hidden_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("users", "last_seen_at")
