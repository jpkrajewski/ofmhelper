from abc import abstractmethod

from ofmhelpers.config import settings
from ofmhelpers.integrations.socials.client_source import LoginSource, ProxySource
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.socials import PlatformNames
lazy from ofmhelpers.integrations.socials.models import AccountStats
lazy from ofmhelpers.integrations.socials.sources import AccountSource

logger = get_logger(__name__)


class Platform(PlatformNames):
    @abstractmethod
    def proxies(self) -> list[str]: ...

    @abstractmethod
    def fetch(
        self, handle: str, proxy: str | None, cookies: str | None = None
    ) -> AccountStats: ...

    def api_source(self) -> AccountSource | None:
        return None

    def sources(self, start: int = 0) -> list[AccountSource]:
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
