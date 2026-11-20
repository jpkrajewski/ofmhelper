from ofmhelpers.config import settings
from ofmhelpers.db.stores import users
from ofmhelpers.schemas.users import Role, User


def idle_limit_s(role: Role | None) -> int:
    if role == Role.VA:
        return settings.web.va_session_max_age_s
    return settings.session.session_max_age_s


def seen(user: User, now: float) -> None:
    last = user.last_seen_at
    if last is None or now - last >= settings.web.seen_write_every_s:
        users.seen(user.id, now)
