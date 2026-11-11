"""
ofmhelpers/web/stores/short_links.py

Links on link providers (/links, routers/tracking/links.py; made, synced
and deleted by features/links/sync.py). Each belongs to a user: `owner` limits a
call to theirs (None: everyone's, for the sweeper). Every provider call
runs on the owner's own key (`features/links/keys.py`).
"""

from datetime import date, timedelta
from functools import lru_cache
lazy from collections.abc import Collection

from ofmhelpers.config import settings
from ofmhelpers.db.repositories import ShortLinkRepository
from ofmhelpers.schemas import ProviderState, ShortLink
from ofmhelpers.schemas.link_catalog import (
    RemoteLink,
    Usage,
    detect_target,
    provider_infos,
)
from ofmhelpers.schemas.links import AccountLinks, PlannedLink, utc_today
from ofmhelpers.schemas.socials import names_of
lazy from ofmhelpers.schemas import GenerateLinks, TrackedAccount
lazy from ofmhelpers.schemas.link_catalog import LinkDays
lazy from ofmhelpers.schemas.links import LinkSyncKey

# The platform links bridge from (Generate links: one per account).
SOURCE_PLATFORM = "instagram"


@lru_cache(maxsize=1)
def _repository() -> ShortLinkRepository:
    """Built on first use rather than at import (constructing it binds a Redis
    connection for its cache)."""
    return ShortLinkRepository()


# --- reads ------------------------------------------------------------------


def _since() -> date:
    return utc_today() - timedelta(days=settings.links.history_days - 1)


def list_links(owner: int | None = None) -> list[ShortLink]:
    """`owner`'s links of a registered provider, newest first, with their
    days of the longest period."""
    return [
        link
        for link in _repository().list_all(owner, _since())
        if link.provider in provider_infos()
    ]


def get_links(ids: list[int], owner: int | None = None) -> list[ShortLink]:
    return [
        link
        for link in _repository().by_ids(ids, owner)
        if link.provider in provider_infos()
    ]


def links_by_account(owner: int | None = None) -> list[AccountLinks]:
    """`owner`'s links that exist on their provider, by the account they
    bridge from (/accounts shows them like its phones)."""
    found: dict[int, AccountLinks] = {}
    for link in list_links(owner):
        if link.account_id is not None and link.status in ("active", "inactive"):
            found.setdefault(
                link.account_id, AccountLinks(account_id=link.account_id, links=[])
            ).links.append(link)
    return list(found.values())


# Links that take a place in the provider's plan: live, or on their way.
_COUNTED = ("active", "inactive", "creating")


def _local_usage(usage: Usage | None, links: list[ShortLink]) -> Usage | None:
    """The plan's limits from the provider's last answer, the counts from
    our own rows: a link deleted (or made) here counts at once, not only
    after the next sync (2026-10-22: 50 / 50 stayed after a delete)."""
    if usage is None:
        return None
    return usage.model_copy(
        update={
            "used": sum(1 for x in links if x.status in _COUNTED),
            "active": (
                sum(1 for x in links if x.status in ("active", "creating"))
                if usage.active is not None
                else None
            ),
        }
    )


def provider_states(user_id: int, keyed: Collection[str]) -> list[ProviderState]:
    """Every provider as /links shows it for `user_id`; `keyed`: the ones
    they have a key for (`features/links/keys.py`)."""
    syncs = {s.provider: s for s in _repository().syncs(user_id)}
    links = list_links(user_id)
    states = []
    for info in provider_infos().all():
        key = info.key
        sync = syncs.get(key)
        has_key = key in keyed
        states.append(
            ProviderState(
                key=key,
                label=info.label,
                has_key=has_key,
                synced_at=sync.synced_at if sync else None,
                syncing=has_key
                and sync is not None
                and (sync.synced_at is None or sync.claimed_at is not None),
                error=sync.error if sync else None,
                usage=_local_usage(
                    sync.usage if sync else None,
                    [x for x in links if x.provider == key],
                ),
            )
        )
    return states


# --- making links -----------------------------------------------------------


def account_slug(provider: str, handle: str) -> str:
    return provider_infos()[provider].slug.make(handle)


def plan(
    user_id: int, form: GenerateLinks, accounts: list[TrackedAccount]
) -> list[PlannedLink]:
    """One new link per account: its slug from the handle, numbered past
    the ones `user_id` has on the provider (per domain or provider-wide,
    as its slugs are unique)."""
    rules = provider_infos()[form.provider].slug
    taken = taken_slugs(user_id, form.provider, form.domain)
    planned = []
    for account in accounts:
        base = rules.make(account.handle)
        slug, n = base, 1
        while slug in taken:
            n += 1
            slug = rules.numbered(base, n)
        taken.add(slug)
        planned.append(
            PlannedLink(
                slug=slug,
                domain=form.domain,
                destination=str(form.destination),
                target=form.target,
                account_id=account.id,
                creator=form.creator,
            )
        )
    return planned


def add_creating(user_id: int, provider: str, links: list[PlannedLink]) -> list[int]:
    return _repository().add_creating(user_id, provider, links)


def created(link_id: int, remote: RemoteLink) -> None:
    _repository().created(link_id, remote.model_dump())


def failed(link_ids: list[int], error: str) -> None:
    _repository().set_status(link_ids, "failed", error)


def set_status(link_ids: list[int], status: str, error: str | None = None) -> None:
    _repository().set_status(link_ids, status, error)


def taken_slugs(user_id: int, provider: str, domain: str) -> set[str]:
    """Slugs of `user_id`'s links a new one on `domain` must not take: on
    that domain, or anywhere on a provider whose slugs are provider-wide."""
    per_domain = provider_infos()[provider].slug.unique_per == "domain"
    return {
        taken.slug
        for taken in _repository().taken(user_id, provider)
        if not per_domain or taken.domain == domain
    }


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
    days: list[LinkDays],
    accounts: list[TrackedAccount],
) -> None:
    """What `provider` lists for `user_id` now (see the repository): a new
    link whose slug is one of `accounts`' handles' slug is put on it."""
    by_slug = {account_slug(provider, a.handle): a.id for a in accounts}
    _repository().record_sync(
        user_id,
        provider,
        [{**r.model_dump(), "target": detect_target(r.destination)} for r in remotes],
        {d.remote_id: [x.model_dump(mode="json") for x in d.days] for d in days},
        by_slug,
        settings.links.create_ttl_s,
    )


def claim_due(pairs: list[LinkSyncKey]) -> list[LinkSyncKey]:
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
    return names_of(SOURCE_PLATFORM).label
