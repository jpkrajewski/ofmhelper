"""Who a request acts as (`schemas.users.Viewer`): the person signed in, or,
for an admin who chose "View as" on /users, that person, looked at and never
changed -- except the admin's own VA, as whom the admin may also act (owner
2026-11-13: the VA's runs are the admin's keys and files anyway). Built once
per request by the auth middleware; everything a route knows about "who"
comes from it (`web/dependencies.py`)."""

from pydantic import BaseModel

from ofmhelpers.db.stores import users
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.schemas.users import Role, SessionUser, User, Viewer


def _as_session(user: User) -> SessionUser:
    return SessionUser(id=user.id, email=user.email, role=user.role)


def _team(owner_id: int) -> frozenset[int]:
    """An owner and every VA of theirs."""
    vas = [va.id for va in users.vas_of(owner_id)]
    return frozenset([owner_id, *vas])


def _own(user: User) -> Viewer:
    """`user` acting as themselves. A VA sees their own jobs and runs on
    their owner's keys; anyone else also sees their VAs' jobs. The team is
    the owner's, the same for the owner and each VA (a VA nobody owns: only
    themselves)."""
    if user.role == Role.VA:
        return Viewer(
            user=_as_session(user),
            user_ids=frozenset({user.id}),
            team_ids=_team(user.owner_id) if user.owner_id else frozenset({user.id}),
            keys_of=user.owner_id,
            label=user.label,
        )
    team = _team(user.id)
    return Viewer(
        user=_as_session(user),
        user_ids=team,
        team_ids=team,
        keys_of=user.id,
        label=user.label,
    )


class Maker(BaseModel):
    """One choice of a gallery's "VAs" filter."""

    id: int
    label: str


def makers(viewer: Viewer) -> list[Maker]:
    """Whose runs the reader can pick out of a gallery: "Me", then their
    VAs by name. Empty without a VA (a VA's own page too): nothing to pick
    between."""
    if viewer.user.role == Role.VA:
        return []
    vas = sorted(users.vas_of(viewer.user.id), key=lambda va: va.label.lower())
    if not vas:
        return []
    me = Maker(id=viewer.user.id, label="Me")
    return [me, *[Maker(id=va.id, label=va.label) for va in vas]]


def viewer_for(user: User, viewing: int | None) -> Viewer:
    """`user`'s request: as themselves, or (an admin with "view as" on, the
    person still there and active) as `viewing`."""
    if viewing is None or not user.is_admin or viewing == user.id:
        return _own(user)
    viewed = users.get_user(viewing)
    if viewed is None or not viewed.active:
        return _own(user)
    theirs = viewed.role == Role.VA and viewed.owner_id == user.id
    return _own(viewed).model_copy(update={"by": _as_session(user), "acts": theirs})


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
