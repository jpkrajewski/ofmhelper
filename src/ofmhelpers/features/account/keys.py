"""Whose key a generation runs on. A person keeps one key per provider slot
on /account (`Provider.key_slots`: APIYI has one per token group); the form
shows one of them, and the run takes the slot of the model picked. A key
typed in that is none of theirs runs as typed, and is kept as theirs once a
run on it succeeds while that slot is empty. An admin with none saved falls
back to the .env key (`Provider.default_key`); nobody else ever gets it."""

from typing import Self

from pydantic import BaseModel, ConfigDict

from ofmhelpers.db.stores import users
from ofmhelpers.integrations.ai_providers.base import Provider, provider_class
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.users import Role, SavedKeys
lazy from ofmhelpers.integrations.ai_providers.tasks import ProviderTask

logger = get_logger(__name__)

# How much of a key is shown or recorded (/account, a job's `key_tail`).
KEY_TAIL = 4


def key_tail(api_key: str) -> str | None:
    """The end of a key a job's row records; None for one too short to
    show a part of without showing most of it."""
    return api_key[-KEY_TAIL:] if len(api_key) >= 2 * KEY_TAIL else None


def _model_id(model: str) -> str:
    """A catalog key (`<provider>:<model id>`) or a bare model id."""
    return model.partition(":")[2] or model


class Keys(BaseModel):
    """One person's saved keys, read once."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    admin: bool
    saved: SavedKeys

    @classmethod
    def of(cls, user_id: int) -> Self:
        """`user_id`'s keys; a VA's are their owner's (none without one),
        never an .env key."""
        user = users.get_user(user_id)
        if user is not None and user.role == Role.VA:
            owner = user.owner_id
            saved = users.api_keys(owner) if owner is not None else SavedKeys()
            return cls(user_id=user_id, admin=False, saved=saved)
        return cls(
            user_id=user_id,
            admin=user is not None and user.is_admin,
            saved=users.api_keys(user_id),
        )

    def _own(self, provider: type[Provider]) -> list[str]:
        """Their saved keys for `provider`, the default slot's first."""
        found = [self.saved.get(slot.id) for slot in provider.key_slots()]
        return [key for key in found if key]

    def _env(self, provider: type[Provider]) -> list[str]:
        """The .env keys an admin falls back to, the form's default first."""
        if not self.admin:
            return []
        default = provider.default_key()
        return [*([default] if default else []), *provider.env_keys()]

    def prefill(self, provider: str) -> str:
        """What a form's key field starts with for `provider`."""
        cls = provider_class(provider)
        return next(iter([*self._own(cls), *self._env(cls)]), "")

    def for_run(self, provider: str, model: str, typed: str) -> str:
        """The key a run of `model` goes out on; "" when there is none. A
        typed key that is none of theirs runs as typed. Otherwise theirs, the
        model's slot first, then their default slot, then the rest, then the
        admin's .env keys: the first one the provider says belongs to the
        model's slot (`Provider.slot_of_key`: a key saved under the wrong
        slot is passed over), else simply the first."""
        cls = provider_class(provider)
        typed = typed.strip()
        known = [*self._own(cls), *self._env(cls)]
        if typed and typed not in known:
            return typed
        wanted = cls.key_slot(_model_id(model))
        first = [self.saved.get(wanted), self.saved.get(cls.key_slots()[0].id)]
        ordered = list(dict.fromkeys(key for key in [*first, *known] if key))
        fitting = (key for key in ordered if cls.slot_of_key(key) == wanted)
        return next(fitting, next(iter(ordered), ""))


def keep_used(user_id: int, made: list[ProviderTask]) -> None:
    """After a run that succeeded: the key each request went out on becomes
    the person's saved key for that slot when they have none there. Never a
    .env key (the admin's fallback stays in .env)."""
    user = users.get_user(user_id)
    if user is None or user.role == Role.VA:
        # A VA's runs go out on the owner's saved keys: nothing to keep.
        return
    saved = users.api_keys(user_id)
    for task in made:
        cls = provider_class(task.provider)
        slot = cls.key_slot(_model_id(task.model))
        if not cls.needs_key or not task.api_key or task.api_key in cls.env_keys():
            continue
        if saved.get(slot):
            continue
        users.set_api_key(user_id, slot, task.api_key)
        saved = users.api_keys(user_id)
        logger.info("user %s: %s key kept from a run", user_id, slot)
