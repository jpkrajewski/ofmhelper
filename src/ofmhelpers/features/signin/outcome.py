"""Where a sign-in step ended, for the route to turn into a response."""

from pydantic import BaseModel

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


def signed_in(user: User, next_url: str, how: str) -> Outcome:
    """`user` got in: audited here; the session is the route's to write."""
    log_event("login", actor=str(user.id), params={"email": user.email, "with": how})
    return Outcome(user=user, redirect=safe_next(next_url))
