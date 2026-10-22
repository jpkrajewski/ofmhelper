"""
ofmhelpers/web/link_sync.py

/links' background work, all on the worker, each on the link owner's own
key (stores.short_links.client):

- `sync_links`: what a provider lists for a user, and every link's daily
  views and clicks of the longest period; a sweep queues one per (user,
  provider) every `sync_interval_s` (3 h), claimed atomically like
  account_tracker's scans (an expired claim is taken over).
- `create_links`: "Generate links" -- each link made in turn, always 18+
  gated (the client refuses to leave one ungated); a taken slug is
  numbered and tried again; a quota, rate limit or key error fails the
  rest at once with the provider's words.
- `enable_adult`, `delete_links`: the bulk actions.

Provider-agnostic: everything provider-specific is behind
`links.providers.LinkProvider`.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.links import (
    AuthError,
    LinkError,
    NewLink,
    QuotaError,
    RateLimitedError,
    SlugTakenError,
    provider_infos,
)
from ofmhelpers.links.providers import link_providers
from ofmhelpers.log import get_logger
from ofmhelpers.web.schemas.links import utc_today
from ofmhelpers.web.stores import short_links as store
from ofmhelpers.web.stores.tracked_accounts import list_accounts

if TYPE_CHECKING:
    from ofmhelpers.links.providers import LinkProvider
    from ofmhelpers.web.schemas import ShortLink

logger = get_logger(__name__)

# Numbered slugs tried after a taken one ("name-2", "name-3", ...).
_SLUG_TRIES = 5
# Errors that would fail every next call the same way.
_STOPS = (AuthError, QuotaError, RateLimitedError)


def sync_links(user_id: int, provider: str) -> None:
    """The worker job: one user's links on one provider and their numbers.
    A failure is recorded on the sync (shown on /links), never raised."""
    found = store.client(user_id, provider)
    if found is None:
        store.finish_sync(user_id, provider, None, "No API key")
        return
    end = utc_today()
    days = min(settings.links.history_days, provider_infos()[provider].max_history_days)
    start = end - timedelta(days=days - 1)
    try:
        usage = found.usage()
        remotes = found.list_links()
        daily = found.daily([r.remote_id for r in remotes], start, end)
    except LinkError as exc:
        logger.warning("%s sync of user %s failed: %s", provider, user_id, exc)
        store.finish_sync(user_id, provider, None, str(exc))
        return
    accounts = store.source_accounts(list_accounts(user_id))
    store.record_sync(user_id, provider, remotes, daily, accounts)
    store.finish_sync(user_id, provider, usage, None)
    logger.info("%s: synced %d link(s) of user %s", provider, len(remotes), user_id)


def _make(provider: LinkProvider, link: ShortLink) -> None:
    """One link, its slug numbered while taken. Raises the provider's
    errors."""
    rules = provider_infos()[link.provider].slug
    base, slug = link.slug, link.slug
    for n in range(1, _SLUG_TRIES + 2):
        try:
            made = provider.create(
                NewLink(
                    slug=slug,
                    domain=link.domain,
                    destination=link.destination,
                    title=f"@{link.account_handle}" if link.account_handle else slug,
                )
            )
        except SlugTakenError:
            if n > _SLUG_TRIES:
                raise
            slug = rules.numbered(base, n + 1)
            store.set_slug(link.id, slug)
            continue
        store.created(link.id, made)
        return


def create_links(user_id: int, provider: str, link_ids: list[int]) -> None:
    """The worker job: "Generate links". Each link is made or failed with
    the reason; one that stops every next call stops the batch."""
    links = [x for x in store.get_links(link_ids, user_id) if x.status == "creating"]
    found = store.client(user_id, provider)
    if found is None:
        store.failed([x.id for x in links], "No API key: add one on /account")
        return
    for done, link in enumerate(links):
        try:
            _make(found, link)
        except _STOPS as exc:
            logger.warning("%s: stopped after %d link(s): %s", provider, done, exc)
            store.failed([x.id for x in links[done:]], str(exc))
            break
        except LinkError as exc:
            store.failed([link.id], str(exc))
    sync_now(user_id, provider)


def enable_adult(user_id: int, link_ids: list[int]) -> None:
    """The worker job: the 18+ confirmation on for each link."""
    for link in store.get_links(link_ids, user_id):
        found = store.client(user_id, link.provider)
        if found is None or link.remote_id is None:
            continue
        try:
            found.enable_adult(link.remote_id)
        except LinkError as exc:
            store.set_status([link.id], link.status, str(exc))
            continue
        store.gated([link.id])


def delete_links(user_id: int, link_ids: list[int]) -> None:
    """The worker job: each link deleted on its provider, then here. One
    the provider still has after a failure goes back to its status with
    the error."""
    for link in store.get_links(link_ids, user_id):
        found = store.client(user_id, link.provider)
        if link.remote_id is None:
            store.removed([link.id])
            continue
        if found is None:
            store.set_status([link.id], "active", "No API key: add one on /account")
            continue
        try:
            found.delete(link.remote_id)
        except LinkError as exc:
            store.set_status([link.id], "active", str(exc))
            continue
        store.removed([link.id])
    sync_now(user_id)


def _queue(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    due = store.claim_due(pairs)
    for user_id, provider in due:
        enqueue(sync_links, user_id, provider)
    if due:
        logger.info("syncing links of %d (user, provider) pair(s)", len(due))
    return due


def queue_due_syncs() -> list[tuple[int, str]]:
    return _queue(store.keyed())


def sync_now(user_id: int, provider: str | None = None) -> list[tuple[int, str]]:
    """`user_id`'s links (on `provider`, else all) synced at once."""
    store.mark_due(user_id, provider)
    return _queue(
        [
            (user_id, key)
            for key in link_providers()
            if (provider is None or key == provider) and store.api_key(user_id, key)
        ]
    )


async def link_sync_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(queue_due_syncs)
        except Exception:
            # A broken sweep must never take the loop down -- log and move on.
            logger.exception("link sync sweep failed")
        await asyncio.sleep(settings.links.sync_check_interval_s)
