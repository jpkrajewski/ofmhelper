"""Who a request acts as (`schemas.users.Viewer`): the person signed in, or,
for an admin who chose "View as" on /users, that person, looked at and never
changed. Built once per request by the auth middleware; everything a route
knows about "who" comes from it (`web/dependencies.py`)."""

from ofmhelpers.db.stores import users
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.schemas.users import Role, SessionUser, User, Viewer


def _as_session(user: User) -> SessionUser:
    return SessionUser(id=user.id, email=user.email, role=user.role)


def _own(user: User) -> Viewer:
    """`user` acting as themselves. A VA sees their own jobs and runs on
    their owner's keys; anyone else also sees their VAs' jobs."""
    if user.role == Role.VA:
        return Viewer(
            user=_as_session(user),
            user_ids=frozenset({user.id}),
            keys_of=user.owner_id,
            label=user.label,
        )
    return Viewer(
        user=_as_session(user),
        user_ids=frozenset({user.id, *(va.id for va in users.vas_of(user.id))}),
        keys_of=user.id,
        label=user.label,
    )


def viewer_for(user: User, viewing: int | None) -> Viewer:
    """`user`'s request: as themselves, or (an admin with "view as" on, the
    person still there and active) as `viewing`."""
    if viewing is None or not user.is_admin or viewing == user.id:
        return _own(user)
    viewed = users.get_user(viewing)
    if viewed is None or not viewed.active:
        return _own(user)
    return _own(viewed).model_copy(update={"by": _as_session(user)})


def viewable(user_id: int, me_id: int) -> User:
    """The person an admin asked to view as: someone else, still active."""
    user = users.get_user(user_id)
    if user is None:
        msg = "No such user"
        raise NotFoundError(msg)
    if user.id == me_id or not user.active:
        msg = "Pick another, active user to view as"
        raise RefusedError(msg)
    return user
