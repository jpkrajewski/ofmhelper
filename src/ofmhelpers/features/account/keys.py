from typing import Self

from pydantic import BaseModel, ConfigDict

from ofmhelpers.db.stores import users
from ofmhelpers.integrations.ai_providers.base import Provider, provider_class
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.users import Role, SavedKeys
lazy from ofmhelpers.integrations.ai_providers.tasks import ProviderTask

logger = get_logger(__name__)

KEY_TAIL = 4


def key_tail(api_key: str) -> str | None:
    return api_key[-KEY_TAIL:] if len(api_key) >= 2 * KEY_TAIL else None


def _model_id(model: str) -> str:
    return model.partition(":")[2] or model


class Keys(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    admin: bool
    saved: SavedKeys

    @classmethod
    def of(cls, user_id: int) -> Self:
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
        found = [self.saved.get(slot.id) for slot in provider.key_slots()]
        return [key for key in found if key]

    def _env(self, provider: type[Provider]) -> list[str]:
        if not self.admin:
            return []
        default = provider.default_key()
        return [*([default] if default else []), *provider.env_keys()]

    def prefill(self, provider: str) -> str:
        cls = provider_class(provider)
        return next(iter([*self._own(cls), *self._env(cls)]), "")

    def for_run(self, provider: str, model: str, typed: str) -> str:
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
    user = users.get_user(user_id)
    if user is None or user.role == Role.VA:
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
