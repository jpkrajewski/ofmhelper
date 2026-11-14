"""A VA who stopped working is logged out, and the day says when.

The session is a cookie: one that just runs out leaves no trace, so "when
did they log out" had no answer unless the VA pressed Logout (owner
2026-11-14). A VA whose last action (`users.last_seen_at`) is older than the
VA's idle limit (`presence.idle_limit_s`, 2 h) and who was not logged out
since gets an `idle_logout` event in the job log, dated at that last action
(that is when they stopped), and every session of theirs is ended
(`users.sessions_ended_at`), on every device.

`end_idle` is the sweep's round (`features/jobs/sweeps.py`); a sign-in calls
`end_if_idle` first, so a VA back after a long break still has the break's
end recorded. Once ended, `sessions_ended_at` is later than `last_seen_at`:
neither writes a second event for the same idle stretch."""

import time

from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import log_event
from ofmhelpers.features.users.presence import idle_limit_s
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.users import Role, User

logger = get_logger(__name__)

IDLE_LOGOUT = "idle_logout"


def _idle(user: User, now: float) -> bool:
    seen, ended = user.last_seen_at, user.sessions_ended_at
    if user.role != Role.VA or seen is None:
        return False
    if ended is not None and ended >= seen:
        return False
    return now - seen > idle_limit_s(user.role)


def end_if_idle(user: User, now: float) -> bool:
    """Records `user`'s idle logout and ends their sessions when they are a
    VA idle past the limit and not logged out since; whether it did."""
    if not _idle(user, now) or user.last_seen_at is None:
        return False
    # The sweep and a sign-in may both get here for one idle stretch: only
    # the one that really ended the sessions records it.
    if not users.end_idle_sessions(user.id):
        return False
    log_event(IDLE_LOGOUT, actor=str(user.id), at=user.last_seen_at)
    logger.info("va %s logged out: idle since %s", user.id, user.last_seen_at)
    return True


def end_idle() -> None:
    """One round of the sweep: every VA idle past the limit."""
    now = time.time()
    for user in users.idle_vas(now - idle_limit_s(Role.VA)):
        end_if_idle(user, now)
