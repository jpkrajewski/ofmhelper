"""Where a sign-in step ended, for the route to turn into a response."""

from pydantic import BaseModel

from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import log_event
from ofmhelpers.features.signin.state import HOME, safe_next
from ofmhelpers.schemas import User


class Outcome(BaseModel):
    """`redirect` set: go there, with a fresh session for `user` if one is
    named. Not set: the form again, showing `error`."""

    redirect: str | None = None
    user: User | None = None
    next_url: str = HOME
    error: str | None = None
    status_code: int = 200


def signed_out(user_id: int) -> None:
    """They pressed Logout: audited, and every session of theirs is ended,
    not only this browser's -- the cookie is the whole session, so a copy of
    it (another device, a stolen one) would otherwise go on working
    (2026-11-14). This browser's cookie is the route's to clear."""
    log_event("logout", actor=str(user_id))
    users.end_sessions(user_id)


def signed_in(user: User, next_url: str, how: str) -> Outcome:
    """`user` got in: audited here; the session is the route's to write."""
    log_event("login", actor=str(user.id), params={"email": user.email, "with": how})
    return Outcome(user=user, redirect=safe_next(next_url))
