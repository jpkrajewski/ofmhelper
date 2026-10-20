"""One background job's proxies, taken in turn, minus the ones found dead.

Shared by every scanner that rotates proxies against a rate-limiting site
(web/reel_watch.py, web/account_tracker.py): a call that fails on one proxy is
retried on the next, and a proxy whose connection itself is broken leaves the
pool for the rest of the job. With none configured, or none left, calls go
direct (`next()` answers None).
"""

from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class ProxyPool:
    def __init__(self, urls: list[str], start: int = 0) -> None:
        """`start`: the first proxy's index, so parallel jobs over the same
        list do not all begin on the same IP."""
        self._live = list(urls)
        self._turn = start % len(self._live) if self._live else 0

    def __len__(self) -> int:
        return len(self._live)

    def next(self) -> str | None:
        if not self._live:
            return None
        self._turn %= len(self._live)
        proxy = self._live[self._turn]
        self._turn += 1
        return proxy

    def drop(self, proxy: str | None) -> None:
        if proxy in self._live:
            # Shift the turn back past a dropped earlier entry, so the next
            # proxy in line is not skipped.
            if self._live.index(proxy) < self._turn:
                self._turn -= 1
            self._live.remove(proxy)
            # Never the URL itself: it carries the proxy's credentials.
            logger.warning("dead proxy skipped for this job; %d left", len(self))
