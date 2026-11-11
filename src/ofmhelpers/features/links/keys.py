"""Whose key a link provider call runs on. Every call runs on the owner's own
key (/account); the agency admin (the password account) with none on the
.env one (`LinkProvider.default_key`); nobody else ever gets the .env key."""

from pydantic import BaseModel

from ofmhelpers.db.stores import short_links as store
from ofmhelpers.db.stores import users
from ofmhelpers.integrations.short_links.providers import (
    get_link_provider,
    link_providers,
)
lazy from ofmhelpers.integrations.short_links.providers import LinkProvider
lazy from ofmhelpers.schemas import ProviderState


class KeyedProvider(BaseModel, frozen=True):
    """A user with a key for a provider."""

    user_id: int
    provider: str


def api_key(user_id: int, provider: str) -> str | None:
    """The key `user_id`'s calls to `provider` run on, if any."""
    # A local provider (self) is served from our own data: it needs no saved
    # key and is always available, its "key" carrying the owner's id.
    local = get_link_provider(provider).local_key(user_id)
    if local is not None:
        return local
    saved = users.api_keys(user_id).get(provider)
    if saved:
        return saved
    user = users.get_user(user_id)
    # The agency admin only: another admin (added on /users) has data of
    # their own and would otherwise sync, see and delete the agency's links.
    if user is not None and user.is_password_account and user.active:
        return get_link_provider(provider).default_key()
    return None


def client(user_id: int, provider: str) -> LinkProvider | None:
    """`provider` on `user_id`'s key; None without one."""
    key = api_key(user_id, provider)
    return get_link_provider(provider)(key) if key else None


def keyed_providers(user_id: int) -> list[str]:
    """The providers `user_id` has a key for, in display order."""
    return [key for key in link_providers().names() if api_key(user_id, key)]


def keyed() -> list[KeyedProvider]:
    """Every active user with a key for a provider."""
    return [
        KeyedProvider(user_id=user.id, provider=provider)
        for user in users.list_users()
        if user.active
        for provider in keyed_providers(user.id)
    ]


def provider_states(user_id: int) -> list[ProviderState]:
    """Every provider as /links shows it for `user_id`."""
    return store.provider_states(user_id, keyed_providers(user_id))
