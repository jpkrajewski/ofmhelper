"""What a platform is to the account tracker: a handle normaliser, a profile
link, its proxies, and one `fetch`. A new platform (TikTok, Threads, X,
Snapchat, ...) is one subclass in its own file plus one entry in
`platforms/__init__.py`'s `_PLATFORM_TYPES`; nothing else changes."""

from abc import ABC, abstractmethod
from typing import ClassVar

from ofmhelpers.socials.models import AccountStats


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
    def fetch(self, handle: str, proxy: str | None) -> AccountStats:
        """The account now, through `proxy` (None: direct). Raises only
        `socials.errors` exceptions."""
