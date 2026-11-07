"""A platform's own client as a source (`Platform.fetch`): each account in
turn through the job's proxies, retried on the next proxy after a jittered
pause, a dead proxy dropped for the rest of the job.

`ProxySource` reads logged out. `LoginSource` reads as the account's owner
(their cookies.txt) and is the last resort: a login is flaky and can get
banned. Every try rate-limited, or even a direct connection down, ends the
source for the rest of the batch -- those accounts go on to the next source."""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, ClassVar

from ofmhelpers.config import settings
from ofmhelpers.integrations.socials.errors import (
    RateLimitedError,
    RetryableError,
    SocialError,
)
from ofmhelpers.integrations.socials.sources import (
    AccountSource,
    Answers,
    Refusal,
    ScanTarget,
)
from ofmhelpers.log import get_logger
from ofmhelpers.utils.proxy_pool import ProxyPool

if TYPE_CHECKING:
    from collections.abc import Callable

    from ofmhelpers.integrations.socials.models import AccountStats

    Fetch = Callable[[str, str | None, str | None], AccountStats]

logger = get_logger(__name__)


class _OfflineError(Exception):
    """Even a direct connection fails: our own network is down, so the rest
    of the batch would only fail the same way."""


def _failure(answered: list[RetryableError], tries: int) -> str:
    """What the row shows once the platform itself answered a failure on
    every try that reached it: the last answer about the account, not a
    rate limit that only says the proxy was busy (a deleted account read
    "rate-limiting" after 3 "not found", dev 2026-11-07). The previous
    numbers stay."""
    told = [f for f in answered if not isinstance(f, RateLimitedError)] or answered
    return f"{told[-1]} ({tries} tries) -- retried at the next rescan"


class ClientSource(AccountSource):
    logged_in: ClassVar[bool]

    def __init__(self, fetch: Fetch, proxies: list[str], start: int = 0) -> None:
        """`start`: the first proxy, so parallel jobs begin on different IPs."""
        self._fetch = fetch
        self._proxies = proxies
        self._start = start

    def scan(self, targets: list[ScanTarget]) -> Answers:
        pool = ProxyPool(self._proxies, self._start)
        answers = Answers()
        # Logged out, a handle two users track is read once for both; a
        # login's read is its owner's only.
        seen: dict[str, AccountStats | Refusal | None] = {}
        for done, target in enumerate(targets):
            if self.logged_in and not target.cookies:
                continue
            cookies = target.cookies if self.logged_in else None
            tries = 1 + min(settings.accounts.proxy_retries, max(len(pool) - 1, 0))
            try:
                if not self.logged_in and target.handle in seen:
                    result = seen[target.handle]
                else:
                    result = self._one(target.handle, pool, tries, cookies)
                    seen[target.handle] = result
            except RateLimitedError:
                logger.warning(
                    "%s rate-limited after %d of %d accounts; the rest go on",
                    self.key,
                    done,
                    len(targets),
                )
                break
            except _OfflineError:
                logger.warning(
                    "%s: network down after %d of %d accounts; the rest go on",
                    self.key,
                    done,
                    len(targets),
                    exc_info=True,
                )
                break
            if isinstance(result, Refusal):
                answers.refusals[target.key] = result
            elif result is not None:
                answers.stats[target.key] = result
        return answers

    def _one(
        self, handle: str, pool: ProxyPool, tries: int, cookies: str | None
    ) -> AccountStats | Refusal | None:
        """One account, up to `tries` times, each through the pool's next
        proxy. None: no try reached the platform (nothing to record).
        Raises RateLimitedError when every try was rate-limited and
        _OfflineError when a direct connection fails."""
        failures: list[RetryableError] = []
        for attempt in range(tries):
            if attempt:
                time.sleep(random.uniform(0, settings.accounts.retry_jitter_s))  # noqa: S311 -- jitter, not crypto
            proxy = pool.next()
            try:
                return self._fetch(handle, proxy, cookies)
            except RetryableError as exc:
                failures.append(exc)
                if not exc.reached and proxy is None:
                    raise _OfflineError from exc
                if exc.dead_proxy:
                    pool.drop(proxy)
                logger.warning(
                    "%s @%s: try %d failed (%s)", self.key, handle, attempt + 1, exc
                )
            except SocialError as exc:
                logger.warning("%s @%s: scan refused: %s", self.key, handle, exc)
                return Refusal(message=str(exc))
        if all(isinstance(f, RateLimitedError) for f in failures):
            raise failures[-1]
        answered = [f for f in failures if f.reached]
        if not answered:
            logger.warning("%s @%s: no try got through", self.key, handle)
            return None
        logger.warning("%s @%s: all %d tries failed", self.key, handle, len(failures))
        return Refusal(message=_failure(answered, len(failures)))


class ProxySource(ClientSource):
    key = "proxy"
    logged_in = False


class LoginSource(ClientSource):
    key = "login"
    logged_in = True
