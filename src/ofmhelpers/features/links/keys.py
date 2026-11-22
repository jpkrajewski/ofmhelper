from pydantic import BaseModel

from ofmhelpers.db.short_links import store
from ofmhelpers.db.users import store as users
from ofmhelpers.integrations.short_links.providers import (
    get_link_provider,
    link_providers,
)
lazy from ofmhelpers.db.short_links.models import ProviderState
lazy from ofmhelpers.integrations.short_links.providers import LinkProvider


class KeyedProvider(BaseModel, frozen=True):
    user_id: int
    provider: str


def api_key(user_id: int, provider: str) -> str | None:
    local = get_link_provider(provider).local_key(user_id)
    if local is not None:
        return local
    saved = users.api_keys(user_id).get(provider)
    if saved:
        return saved
    user = users.get_user(user_id)
    if user is not None and user.is_password_account and user.active:
        return get_link_provider(provider).default_key()
    return None


def client(user_id: int, provider: str) -> LinkProvider | None:
    key = api_key(user_id, provider)
    return get_link_provider(provider)(key) if key else None


def keyed_providers(user_id: int) -> list[str]:
    return [key for key in link_providers() if api_key(user_id, key)]


def keyed() -> list[KeyedProvider]:
    return [
        KeyedProvider(user_id=user.id, provider=provider)
        for user in users.list_users()
        if user.active
        for provider in keyed_providers(user.id)
    ]


def provider_states(user_id: int) -> list[ProviderState]:
    return store.provider_states(user_id, keyed_providers(user_id))
