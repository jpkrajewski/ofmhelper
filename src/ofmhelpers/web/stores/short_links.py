"""
ofmhelpers/web/stores/short_links.py

Links on link providers (/links, routers/tracking/links.py; made, synced
and deleted by web/link_sync.py). Each belongs to a user: `owner` limits a
call to theirs (None: everyone's, for the sweeper). Every provider call
runs on the owner's own key (/account), an admin with none on the .env one
(`LinkProvider.default_key`); a user never gets the .env key.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.config import settings
from ofmhelpers.links import RemoteLink, Usage, detect_target, provider_infos
from ofmhelpers.links.providers import get_link_provider, link_providers
from ofmhelpers.socials import get_platform
from ofmhelpers.web.db.repositories import ShortLinkRepository
from ofmhelpers.web.schemas import ProviderState, ShortLink
from ofmhelpers.web.schemas.links import utc_today
from ofmhelpers.web.stores import users

if TYPE_CHECKING:
    from ofmhelpers.links import DayStat
    from ofmhelpers.links.providers import LinkProvider
    from ofmhelpers.web.schemas import GenerateLinks, TrackedAccount

# The platform links bridge from (Generate links: one per account).
SOURCE_PLATFORM = "instagram"


@lru_cache(maxsize=1)
def _repository() -> ShortLinkRepository:
    """Built on first use rather than at import (constructing it binds a Redis
    connection for its cache)."""
    return ShortLinkRepository()


# --- keys -------------------------------------------------------------------


def api_key(user_id: int, provider: str) -> str | None:
    """The key `user_id`'s calls to `provider` run on, if any."""
    saved = users.api_keys(user_id).get(provider)
    if saved:
        return saved
    user = users.get_user(user_id)
    if user is not None and user.is_admin and user.active:
        return get_link_provider(provider).default_key()
    return None


def client(user_id: int, provider: str) -> LinkProvider | None:
    """`provider` on `user_id`'s key; None without one."""
    key = api_key(user_id, provider)
    return get_link_provider(provider)(key) if key else None


def keyed() -> list[tuple[int, str]]:
    """(user, provider) of every active user with a key for it."""
    return [
        (user.id, provider)
        for user in users.list_users()
        if user.active
        for provider in link_providers()
        if api_key(user.id, provider)
    ]


# --- reads ------------------------------------------------------------------


def _since() -> date:
    return utc_today() - timedelta(days=settings.links.history_days - 1)


def list_links(owner: int | None = None) -> list[ShortLink]:
    """`owner`'s links of a registered provider, newest first, with their
    days of the longest period."""
    return [
        ShortLink.model_validate(row)
        for row in _repository().list_all(owner, _since())
        if row["provider"] in link_providers()
    ]


def get_links(ids: list[int], owner: int | None = None) -> list[ShortLink]:
    return [
        ShortLink.model_validate(row)
        for row in _repository().by_ids(ids, owner)
        if row["provider"] in link_providers()
    ]


def links_by_account(owner: int | None = None) -> dict[int, list[ShortLink]]:
    """`owner`'s links that exist on their provider, by the account they
    bridge from (/accounts shows them like its phones)."""
    found: dict[int, list[ShortLink]] = {}
    for link in list_links(owner):
        if link.account_id is not None and link.status in ("active", "inactive"):
            found.setdefault(link.account_id, []).append(link)
    return found


def provider_states(user_id: int) -> list[ProviderState]:
    syncs = {s["provider"]: s for s in _repository().syncs(user_id)}
    states = []
    for key, info in provider_infos().items():
        if key not in link_providers():
            continue
        sync = syncs.get(key, {})
        usage = sync.get("usage")
        states.append(
            ProviderState(
                key=key,
                label=info.label,
                has_key=api_key(user_id, key) is not None,
                synced_at=sync.get("synced_at"),
                error=sync.get("error"),
                usage=Usage.model_validate(usage) if usage else None,
            )
        )
    return states


# --- making links -----------------------------------------------------------


def account_slug(provider: str, handle: str) -> str:
    return provider_infos()[provider].slug.make(handle)


def plan(
    user_id: int, form: GenerateLinks, accounts: list[TrackedAccount]
) -> list[dict]:
    """One new link per account: its slug from the handle, numbered past
    the ones `user_id` has on the provider (per domain or provider-wide,
    as its slugs are unique)."""
    rules = provider_infos()[form.provider].slug
    per_domain = rules.unique_per == "domain"
    taken = {
        slug
        for domain, slug in _repository().taken(user_id, form.provider)
        if not per_domain or domain == form.domain
    }
    planned = []
    for account in accounts:
        base = rules.make(account.handle)
        slug, n = base, 1
        while slug in taken:
            n += 1
            slug = rules.numbered(base, n)
        taken.add(slug)
        planned.append(
            {
                "slug": slug,
                "domain": form.domain,
                "destination": str(form.destination),
                "target": form.target,
                "account_id": account.id,
                "creator": form.creator,
            }
        )
    return planned


def add_creating(user_id: int, provider: str, links: list[dict]) -> list[int]:
    return _repository().add_creating(user_id, provider, links)


def created(link_id: int, remote: RemoteLink) -> None:
    _repository().created(link_id, remote.model_dump())


def failed(link_ids: list[int], error: str) -> None:
    _repository().set_status(link_ids, "failed", error)


def set_status(link_ids: list[int], status: str, error: str | None = None) -> None:
    _repository().set_status(link_ids, status, error)


def set_slug(link_id: int, slug: str) -> None:
    _repository().set_slug(link_id, slug)


def gated(link_ids: list[int]) -> None:
    _repository().set_adult(link_ids)


def removed(link_ids: list[int]) -> None:
    _repository().remove(link_ids)


def assign_creator(
    link_ids: list[int], creator: str | None, owner: int | None = None
) -> int:
    return _repository().set_creator(link_ids, creator, owner)


# --- syncs ------------------------------------------------------------------


def record_sync(
    user_id: int,
    provider: str,
    remotes: list[RemoteLink],
    days: dict[str, list[DayStat]],
    accounts: list[TrackedAccount],
) -> None:
    """What `provider` lists for `user_id` now (see the repository): a new
    link whose slug is one of `accounts`' handles' slug is put on it."""
    by_slug = {account_slug(provider, a.handle): a.id for a in accounts}
    _repository().record_sync(
        user_id,
        provider,
        [{**r.model_dump(), "target": detect_target(r.destination)} for r in remotes],
        {k: [d.model_dump(mode="json") for d in v] for k, v in days.items()},
        by_slug,
        settings.links.create_ttl_s,
    )


def claim_due(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    s = settings.links
    return _repository().claim_due(pairs, s.sync_interval_s, s.claim_ttl_s)


def mark_due(user_id: int, provider: str | None = None) -> None:
    _repository().mark_due(user_id, provider)


def finish_sync(
    user_id: int, provider: str, usage: Usage | None, error: str | None
) -> None:
    _repository().finish_sync(
        user_id, provider, usage.model_dump() if usage else None, error
    )


def source_accounts(accounts: list[TrackedAccount]) -> list[TrackedAccount]:
    """The accounts links bridge from."""
    return [a for a in accounts if a.platform == SOURCE_PLATFORM]


def source_label() -> str:
    return get_platform(SOURCE_PLATFORM).label
