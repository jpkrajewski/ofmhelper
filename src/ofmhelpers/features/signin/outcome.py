import time

from pydantic import BaseModel

from ofmhelpers.db.jobs.store import log_event
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import User
from ofmhelpers.features.signin.state import HOME, safe_next
from ofmhelpers.features.users.idle import end_if_idle
from ofmhelpers.features.vas import notices


class Outcome(BaseModel):
    redirect: str | None = None
    user: User | None = None
    next_url: str = HOME
    error: str | None = None
    status_code: int = 200


def signed_out(user_id: int) -> None:
    log_event("logout", actor=str(user_id))
    users.end_sessions(user_id)


def signed_in(user: User, next_url: str, how: str) -> Outcome:
    now = time.time()
    end_if_idle(user, now)
    users.seen(user.id, now)
    log_event("login", actor=str(user.id), params={"email": user.email, "with": how})
    notices.logged_in(user)
    return Outcome(user=user, redirect=safe_next(next_url))
