from datetime import timedelta
lazy from collections.abc import Callable

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.short_links import store
from ofmhelpers.db.short_links.schema import LinkSyncKey, utc_today
from ofmhelpers.db.tracked_accounts.store import list_accounts
from ofmhelpers.features.links import keys
from ofmhelpers.integrations.short_links import (
    AuthError,
    LinkError,
    NewLink,
    QuotaError,
    RateLimitedError,
    SlugTakenError,
    UngatedError,
    provider_infos,
)
from ofmhelpers.integrations.short_links.providers import link_providers
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.short_links.models import ShortLink
lazy from ofmhelpers.integrations.short_links.providers import LinkProvider

logger = get_logger(__name__)

_SLUG_TRIES = 5
_STOPS = (AuthError, QuotaError, RateLimitedError)


def _regate(found: LinkProvider, user_id: int, provider: str) -> None:
    for link in store.list_links(user_id):
        if link.provider != provider or link.adult is not False or not link.remote_id:
            continue
        try:
            found.enable_adult(link.remote_id)
        except LinkError as exc:
            logger.warning(
                "%s: 18+ gate on %s failed: %s",
                provider,
                link.address,
                exc,
                exc_info=True,
            )
            continue
        store.gated([link.id])


def sync_links(user_id: int, provider: str) -> None:
    found = keys.client(user_id, provider)
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
        accounts = store.source_accounts(list_accounts(user_id))
        store.record_sync(user_id, provider, remotes, daily, accounts)
        _regate(found, user_id, provider)
    except LinkError as exc:
        logger.warning(
            "%s sync of user %s failed: %s", provider, user_id, exc, exc_info=True
        )
        store.finish_sync(user_id, provider, None, str(exc))
        return
    except Exception as exc:
        logger.exception("%s sync of user %s failed", provider, user_id)
        store.finish_sync(
            user_id, provider, None, f"Sync failed ({type(exc).__name__}): {exc}"
        )
        return
    store.finish_sync(user_id, provider, usage, None)
    logger.info("%s: synced %d link(s) of user %s", provider, len(remotes), user_id)


def _make(provider: LinkProvider, link: ShortLink, taken: set[str]) -> None:
    rules = provider_infos()[link.provider].slug
    base, slug, n = link.slug, link.slug, 1
    for _ in range(_SLUG_TRIES + 1):
        try:
            made = provider.create(
                NewLink(
                    slug=slug,
                    domain=link.domain,
                    destination=link.destination,
                    title=f"@{link.account_handle}" if link.account_handle else slug,
                )
            )
        except SlugTakenError:  # silent: taken: the next number is tried
            taken.add(slug)
            while slug in taken:
                n += 1
                slug = rules.numbered(base, n)
            store.set_slug(link.id, slug)
            taken.add(slug)
            continue
        except UngatedError as exc:
            logger.warning("%s: live without the 18+ gate", link.address, exc_info=True)
            store.created(link.id, exc.remote)
            store.set_status([link.id], "active", str(exc))
            return
        store.created(link.id, made)
        return
    msg = f"{link.provider_label}: every slug tried for {base} is taken"
    raise SlugTakenError(msg)


def create_links(user_id: int, provider: str, link_ids: list[int]) -> None:
    links = [x for x in store.get_links(link_ids, user_id) if x.status == "creating"]
    found = keys.client(user_id, provider)
    if found is None:
        store.failed([x.id for x in links], "No API key: add one on /account")
        return
    try:
        for done, link in enumerate(links):
            taken = store.taken_slugs(user_id, provider, link.domain) - {link.slug}
            try:
                _make(found, link, taken)
            except _STOPS as exc:
                logger.warning(
                    "%s: stopped after %d link(s): %s",
                    provider,
                    done,
                    exc,
                    exc_info=True,
                )
                store.failed([x.id for x in links[done:]], str(exc))
                break
            except LinkError as exc:
                logger.warning(
                    "%s: making %s failed: %s",
                    provider,
                    link.address,
                    exc,
                    exc_info=True,
                )
                store.failed([link.id], str(exc))
            except Exception as exc:
                logger.exception("%s: making %s failed", provider, link.address)
                store.failed([link.id], f"Failed ({type(exc).__name__}): {exc}")
    finally:
        sync_now(user_id, provider)


def _clients(user_id: int) -> Callable[[str], LinkProvider | None]:
    made: dict[str, LinkProvider | None] = {}

    def client(provider: str) -> LinkProvider | None:
        if provider not in made:
            made[provider] = keys.client(user_id, provider)
        return made[provider]

    return client


def enable_adult(user_id: int, link_ids: list[int]) -> None:
    client = _clients(user_id)
    for link in store.get_links(link_ids, user_id):
        found = client(link.provider)
        if found is None or link.remote_id is None:
            continue
        try:
            found.enable_adult(link.remote_id)
        except LinkError as exc:
            logger.warning(
                "adult gate of %s failed: %s", link.address, exc, exc_info=True
            )
            store.set_status([link.id], link.status, str(exc))
            continue
        store.gated([link.id])


def delete_links(user_id: int, link_ids: list[int]) -> None:
    client = _clients(user_id)
    try:
        for link in store.get_links(link_ids, user_id):
            found = client(link.provider)
            if link.remote_id is None:
                store.removed([link.id])
                continue
            if found is None:
                store.set_status([link.id], "active", "No API key: add one on /account")
                continue
            try:
                found.delete(link.remote_id)
            except LinkError as exc:
                logger.warning(
                    "deleting %s failed: %s", link.address, exc, exc_info=True
                )
                store.set_status([link.id], "active", str(exc))
                continue
            store.removed([link.id])
    finally:
        sync_now(user_id)


def _queue(wanted: list[LinkSyncKey]) -> list[LinkSyncKey]:
    due = store.claim_due(wanted)
    for key in due:
        enqueue(sync_links, key.user_id, key.provider)
    if due:
        logger.info("syncing links of %d (user, provider) pair(s)", len(due))
    return due


def queue_due_syncs() -> list[LinkSyncKey]:
    return _queue(
        [LinkSyncKey(user_id=k.user_id, provider=k.provider) for k in keys.keyed()]
    )


def sync_now(user_id: int, provider: str | None = None) -> list[LinkSyncKey]:
    store.mark_due(user_id, provider)
    return _queue(
        [
            LinkSyncKey(user_id=user_id, provider=key)
            for key in link_providers()
            if (provider is None or key == provider) and keys.api_key(user_id, key)
        ]
    )
