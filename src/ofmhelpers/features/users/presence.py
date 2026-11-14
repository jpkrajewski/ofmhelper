"""When a person last did something, and how long they may sit idle.

`seen` keeps `users.last_seen_at` to the minute: the auth middleware calls
it on every action (a page opened, anything sent; never a script's poll) and
it writes only when the stored moment is older than
`settings.web.seen_write_every_s`, so a busy person costs one write a minute,
not one a click. Read by the idle sweep (`features/users/idle.py`) and by
/vas ("Active 14:05").

`idle_limit_s`: a VA is out after `settings.web.va_session_max_age_s` (2 h)
without an action (owner 2026-11-14: "shorter log out - when they dont do
stuff for 2 hours, i mean generating clicking"); everyone else after the
session's own lifetime (5 h)."""

from ofmhelpers.config import settings
from ofmhelpers.db.stores import users
from ofmhelpers.schemas.users import Role, User


def idle_limit_s(role: Role | None) -> int:
    """Seconds without an action after which a session of `role` ends.
    None (nobody signed in): the session's own lifetime."""
    if role == Role.VA:
        return settings.web.va_session_max_age_s
    return settings.session.session_max_age_s


def seen(user: User, now: float) -> None:
    """`user` did something at `now`."""
    last = user.last_seen_at
    if last is None or now - last >= settings.web.seen_write_every_s:
        users.seen(user.id, now)
