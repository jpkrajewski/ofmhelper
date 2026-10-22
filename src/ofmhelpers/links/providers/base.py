"""What a link provider is to /links: make a link (always 18+ gated), list
them, read their daily views and clicks, turn the 18+ gate on, delete one,
and the plan's quota. A new provider is one subclass in its own file plus
its class in `providers/__init__.py`'s `_PROVIDER_TYPES` and its
`providers.json` entry; nothing else changes."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

import requests

from ofmhelpers.config import settings
from ofmhelpers.links import provider_infos
from ofmhelpers.links.errors import AuthError, LinkError, QuotaError, RateLimitedError
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound

if TYPE_CHECKING:
    from datetime import date

    from ofmhelpers.links.models import (
        DayStat,
        NewLink,
        ProviderInfo,
        RemoteLink,
        Usage,
    )

logger = get_logger(__name__)

_UNAUTHORIZED = 401
_PAYMENT_REQUIRED = 402
_TOO_MANY = 429


class LinkProvider(ABC):
    """One account on one provider, built per job with the key it runs on."""

    # Stored on every link; never rename one in use.
    key: ClassVar[str]

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._session = outbound.mount(requests.Session())
        self._last_call = 0.0

    @classmethod
    def info(cls) -> ProviderInfo:
        return provider_infos()[cls.key]

    @classmethod
    def label(cls) -> str:
        return cls.info().label

    @classmethod
    def default_key(cls) -> str | None:
        """The key an admin runs on when they saved none of their own
        (.env). Users only ever run on their own."""
        return settings.links.admin_key(cls.key)

    @abstractmethod
    def _headers(self) -> dict[str, str]: ...

    def _error(self, response: requests.Response) -> str:
        """The provider's own words for a failed call."""
        try:
            body = response.json()
        except ValueError:
            return response.text[:200] or response.reason
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                error = error.get("message")
            message = body.get("message")
            details = body.get("details")
            parts = [str(p) for p in (error, message) if p]
            if isinstance(details, list) and details:
                parts.append("; ".join(str(d) for d in details[:3]))
            if parts:
                return ": ".join(dict.fromkeys(parts))
        return str(body)[:200]

    def _call(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """One call, `min_interval_s` after the previous one. Raises
        AuthError (401), QuotaError (402), RateLimitedError (429) and
        LinkError for no answer at all; other statuses are the caller's."""
        info = self.info()
        wait = self._last_call + info.min_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        try:
            response = self._session.request(
                method,
                info.base_url + path,
                headers=self._headers(),
                timeout=settings.links.request_timeout_s,
                **kwargs,
            )
        except requests.RequestException as exc:
            logger.warning("%s %s %s failed", self.key, method, path, exc_info=True)
            msg = f"{info.label} did not answer ({type(exc).__name__}): try again"
            raise LinkError(msg) from exc
        if response.status_code == _UNAUTHORIZED:
            msg = f"{info.label} refused the API key: {self._error(response)}"
            raise AuthError(msg)
        if response.status_code == _PAYMENT_REQUIRED:
            msg = f"{info.label} plan limit reached: {self._error(response)}"
            raise QuotaError(msg)
        if response.status_code == _TOO_MANY:
            msg = f"{info.label} rate limit reached: {self._error(response)}"
            raise RateLimitedError(msg)
        return response

    def _failed(self, response: requests.Response, what: str) -> LinkError:
        logger.warning(
            "%s %s failed: HTTP %s %s",
            self.key,
            what,
            response.status_code,
            self._error(response),
        )
        return LinkError(f"{self.label()} could not {what}: {self._error(response)}")

    @abstractmethod
    def create(self, link: NewLink) -> RemoteLink:
        """Makes the link, visitors confirming 18+ before the redirect.
        Raises SlugTakenError, QuotaError, ... -- and LinkError when the
        gate could not be set (the link is deleted again then: none goes
        live ungated)."""

    @abstractmethod
    def list_links(self) -> list[RemoteLink]:
        """Every link of the account."""

    @abstractmethod
    def daily(
        self, remote_ids: list[str], start: date, end: date
    ) -> dict[str, list[DayStat]]:
        """Per link, its days from `start` to `end` (UTC, both included)
        that had any traffic."""

    @abstractmethod
    def enable_adult(self, remote_id: str) -> None:
        """Turns the 18+ confirmation on."""

    @abstractmethod
    def delete(self, remote_id: str) -> None:
        """Deletes the link for good; one already gone is fine."""

    @abstractmethod
    def usage(self) -> Usage: ...
