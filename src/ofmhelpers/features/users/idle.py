# Copyright (C) 2026 jpkrajewski

import time

from ofmhelpers.db.jobs.store import log_event
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role, User
from ofmhelpers.features.users.presence import idle_limit_s
from ofmhelpers.log import get_logger

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
    if not _idle(user, now) or user.last_seen_at is None:
        return False
    if not users.end_idle_sessions(user.id):
        return False
    log_event(IDLE_LOGOUT, actor=str(user.id), at=user.last_seen_at)
    logger.info("va %s logged out: idle since %s", user.id, user.last_seen_at)
    return True


def end_idle() -> None:
    now = time.time()
    for user in users.idle_vas(now - idle_limit_s(Role.VA)):
        end_if_idle(user, now)
