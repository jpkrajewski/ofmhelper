# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel

from ofmhelpers.db.jobs.store import log_event
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role, SessionUser, Standing, User, Viewer
from ofmhelpers.features.refusals import NotFoundError, RefusedError


def _as_session(user: User) -> SessionUser:
    return SessionUser(id=user.id, email=user.email, role=user.role)


def _team(owner_id: int) -> frozenset[int]:
    vas = [va.id for va in users.vas_of(owner_id)]
    return frozenset([owner_id, *vas])


def _own(user: User) -> Viewer:
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
    id: int
    label: str


def makers(viewer: Viewer) -> list[Maker]:
    if viewer.user.role == Role.VA:
        return []
    vas = sorted(users.vas_of(viewer.user.id), key=lambda va: va.label.lower())
    if not vas:
        return []
    me = Maker(id=viewer.user.id, label="Me")
    return [me, *[Maker(id=va.id, label=va.label) for va in vas]]


def _managing(manager: User) -> Viewer:
    employer = users.get_user(manager.owner_id) if manager.owner_id else None
    seen = employer if employer is not None and employer.active else manager
    return _own(seen).model_copy(
        update={
            "by": _as_session(manager),
            "acts": False,
            "pages": Role.MANAGER,
            "keys_of": None,
            "label": manager.label,
        },
    )


def viewer_for(user: User, viewing: int | None) -> Viewer:
    if user.role == Role.MANAGER:
        return _managing(user)
    if viewing is None or not user.is_admin or viewing == user.id:
        return _own(user)
    viewed = users.get_user(viewing)
    if viewed is None or not viewed.active:
        return _own(user)
    theirs = viewed.role == Role.VA and viewed.owner_id == user.id
    return _own(viewed).model_copy(update={"by": _as_session(user), "acts": theirs})


def viewable(user_id: int, me_id: int) -> User:
    user = users.get_user(user_id)
    if user is None:
        msg = "No such user"
        raise NotFoundError(msg)
    if user.id == me_id or not user.active:
        msg = "Pick another, active user to view as"
        raise RefusedError(msg)
    return user


class Seated(BaseModel):
    user: User
    standing: Standing


def signed_in_user(user_id: int) -> Seated | None:
    user = users.get_user(user_id)
    standing = users.standing(user_id)
    if user is None or standing is None:
        return None
    return Seated(user=user, standing=standing)


def _admin(admin_id: int | None) -> str | None:
    return None if admin_id is None else str(admin_id)


def viewing_started(admin_id: int | None, user: User) -> None:
    log_event(
        "viewing",
        actor=_admin(admin_id),
        params={"viewing": user.id, "email": user.email},
    )


def viewing_ended(admin_id: int | None, viewed: int) -> None:
    log_event("viewing_end", actor=_admin(admin_id), params={"viewing": viewed})
