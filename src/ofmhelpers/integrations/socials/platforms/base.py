"""What a platform is to the account tracker: a handle normaliser, a profile
link, its proxies, its own client (`fetch`) and the sources a scan asks in
turn (`sources`). A new platform (TikTok, Threads, X, Facebook, ...) is one
subclass in its own file plus one entry in `platforms/__init__.py`'s
`_PLATFORM_TYPES`; a scraping API for it is one `AccountSource` returned by
`api_source`. Nothing else changes."""

from abc import ABC, abstractmethod
from typing import ClassVar

from ofmhelpers.config import settings
from ofmhelpers.integrations.socials.client_source import LoginSource, ProxySource
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.socials.models import AccountStats
lazy from ofmhelpers.integrations.socials.sources import AccountSource

logger = get_logger(__name__)


class Platform(ABC):
    # Stored on every tracked account; never rename one in use.
    key: ClassVar[str]
    label: ClassVar[str]

    @abstractmethod
    def handle(self, text: str) -> str:
        """`text` as pasted ("name", "@name", a profile URL) reduced to the
        stored handle. Raises ValueError when it is none."""

    @abstractmethod
    def profile_url(self, handle: str) -> str: ...

    @abstractmethod
    def proxies(self) -> list[str]:
        """Proxy URLs a scan rotates through; empty: direct."""

    @abstractmethod
    def fetch(
        self, handle: str, proxy: str | None, cookies: str | None = None
    ) -> AccountStats:
        """The account now through our own client, via `proxy` (None:
        direct), logged in with `cookies` (the account owner's own
        cookies.txt text, None: logged out) where the platform has use for
        a login. Raises only `socials.errors` exceptions."""

    def api_source(self) -> AccountSource | None:
        """A scraping API that reads this platform (Apify, ...); None: none."""
        return None

    def sources(self, start: int = 0) -> list[AccountSource]:
        """The sources a scan asks, in `OFM_ACCOUNTS_SOURCES` order (`api`,
        `proxy`, `login`); one the platform lacks is left out. `start`: the
        first proxy, so parallel jobs begin on different IPs."""
        found: list[AccountSource] = []
        for name in settings.accounts.source_list():
            if name == "api":
                api = self.api_source()
                if api is not None:
                    found.append(api)
            elif name == "proxy":
                found.append(ProxySource(self.fetch, self.proxies(), start))
            elif name == "login":
                found.append(LoginSource(self.fetch, self.proxies(), start))
        return found
