from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class ProxyPool:
    def __init__(self, urls: list[str], start: int = 0) -> None:
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
            if self._live.index(proxy) < self._turn:
                self._turn -= 1
            self._live.remove(proxy)
            logger.warning("dead proxy skipped for this job; %d left", len(self))
