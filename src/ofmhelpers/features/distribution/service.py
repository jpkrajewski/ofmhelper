import datetime
import time
from typing import TYPE_CHECKING, BinaryIO

from pydantic import BaseModel, SkipValidation

from ofmhelpers.config import settings
from ofmhelpers.db.distribution import store
from ofmhelpers.db.self_links import store as self_links
from ofmhelpers.db.short_links.schema import SelfTotals
from ofmhelpers.features.distribution import sweep
from ofmhelpers.features.distribution.presets import presets_of
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.features.storage.service import StorageService
from ofmhelpers.integrations.cookie_jar import CookieFileError
from ofmhelpers.integrations.pinterest import (
    PinterestClient,
    PinterestError,
    only_pinterest,
    session_expires_at,
)
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from ofmhelpers.db.distribution.schema import Channel, PostCounts

logger = get_logger(__name__)

PLATFORM = "pinterest"
NOT_LOGGED_IN = "Not logged in to Pinterest: log in on pinterest.com and export again"
FAILED = "Something went wrong with this file"
TOO_BIG = "Not a cookies.txt (over 20 MB)"
_MAX_COOKIES = 20_000_000
_WEEK_DAYS = 7
_EXPIRY_WARN_S = 7 * 86400
_FAILING_S = 86400


class Upload(BaseModel, arbitrary_types_allowed=True):
    name: str
    file: SkipValidation[BinaryIO]


class Added(BaseModel):
    file: str
    account: str = ""
    queued: int = 0
    error: str | None = None


class Health(BaseModel):
    ok: bool
    headline: str
    problems: list[str] = []
    last_pin_at: float | None = None


class DistributionPage(BaseModel):
    health: Health
    pinned_today: int
    clicks_today: SelfTotals
    clicks_week: SelfTotals
    working: int
    accounts: int
    models: int
    added: list[Added] = []
    error: str | None = None


def _names(channels: list[Channel]) -> str:
    return ", ".join(f"@{c.account_name}" for c in channels)


def _health(channels: list[Channel], counts: dict[int, PostCounts]) -> Health:
    now = time.time()
    if not channels:
        return Health(
            ok=False, headline="No Pinterest accounts yet: upload their cookies"
        )
    stopped = [c for c in channels if c.error]
    waiting = [c for c in channels if c.resume_at is not None and c.resume_at > now]
    running = [c for c in channels if not c.error and not c.paused]
    empty = [c for c in running if counts[c.id].queued == 0]
    problems = []
    if stopped:
        problems.append(f"Need new cookies (logged out): {_names(stopped)}")
    if waiting:
        problems.append(
            f"Rate limited by Pinterest, waiting on their own: {_names(waiting)}"
        )
    if empty:
        problems.append(f"Nothing left to pin (add reels to POSTED): {_names(empty)}")
    soon = now + _EXPIRY_WARN_S
    expiring = [
        c
        for c in running
        if c.cookies_expire_at is not None and c.cookies_expire_at < soon
    ]
    if expiring:
        problems.append(
            f"Cookies expire within 7 days, upload new ones: {_names(expiring)}"
        )
    by_id = {c.id: c for c in running}
    problems.extend(
        f"Every pin failed in the last 24 h: @{by_id[bad.channel_id].account_name}"
        f" ({bad.error[:120]})"
        for bad in store.repository().failing(list(by_id), _FAILING_S)
    )
    if not settings.distribution.scheduled(settings.infra.production):
        problems.append("Pins go out by themselves on the live server only, not here")
    last = store.repository().last_published([c.id for c in channels])
    if not running:
        headline = "Not pinning: every account is stopped"
    else:
        headline = f"Working: {len(running)} of {len(channels)} accounts pinning"
    return Health(
        ok=bool(running) and not stopped and not empty and not expiring,
        headline=headline,
        problems=problems,
        last_pin_at=last,
    )


class Distribution(BaseModel, frozen=True):
    uid: int
    scope: int
    storage: StorageService

    def page(
        self, added: list[Added] | None = None, error: str | None = None
    ) -> DistributionPage:
        repo = store.repository()
        channels = repo.channels_of(self.scope)
        counts = {c.id: repo.counts(c.id) for c in channels}
        links = [m.link_id for m in repo.model_links(self.scope)]
        today = datetime.datetime.now(datetime.UTC).date()
        week = today - datetime.timedelta(days=_WEEK_DAYS - 1)
        return DistributionPage(
            health=_health(channels, counts),
            pinned_today=sum(c.today for c in counts.values()),
            clicks_today=self_links.totals(links, today, today),
            clicks_week=self_links.totals(links, week, today),
            working=sum(1 for c in channels if not c.error and not c.paused),
            accounts=len(channels),
            models=len(presets_of(self.scope)),
            added=added or [],
            error=error,
        )

    def add(self, uploads: list[Upload]) -> list[Added]:
        added = [self._add_one(u) for u in uploads]
        repo = store.repository()
        new = [
            c
            for c in repo.channels_of(self.scope)
            if c.account_name in {a.account for a in added}
        ]
        if new:
            sweep.fill_many(self.storage, new)
        queued = {c.account_name: repo.counts(c.id).queued for c in new}
        return [
            a.model_copy(update={"queued": queued.get(a.account, 0)}) for a in added
        ]

    def _add_one(self, upload: Upload) -> Added:
        try:
            return self._connect(upload)
        except (RefusedError, PinterestError, CookieFileError) as exc:
            logger.warning(
                "distribution: %s not added: %s", upload.name, exc, exc_info=True
            )
            return Added(file=upload.name, error=str(exc))
        except Exception:
            logger.exception("distribution: adding %s failed", upload.name)
            return Added(file=upload.name, error=FAILED)

    def _connect(self, upload: Upload) -> Added:
        raw = upload.file.read(_MAX_COOKIES + 1)
        if len(raw) > _MAX_COOKIES:
            raise RefusedError(TOO_BIG)
        cookies = only_pinterest(raw.decode("utf-8", errors="replace"))
        client = PinterestClient(cookies)
        if not client.signed_in:
            raise RefusedError(NOT_LOGGED_IN)
        account = client.me()
        store.repository().connect(
            self.scope, PLATFORM, account.username, cookies, session_expires_at(cookies)
        )
        logger.info("distribution: @%s added", account.username)
        return Added(file=upload.name, account=account.username)
