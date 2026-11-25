# Copyright (C) 2026 jpkrajewski

import hashlib
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar
lazy from datetime import date

import requests
from pydantic import BaseModel, ValidationError
from redis import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.short_links import provider_infos
from ofmhelpers.integrations.short_links.errors import (
    AuthError,
    LinkError,
    QuotaError,
    RateLimitedError,
)
lazy from ofmhelpers.db.short_links.catalog.schema import (
    LinkDays,
    LinkHours,
    NewLink,
    ProviderInfo,
    RemoteLink,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)


class RequestHeaders(BaseModel):
    pass


def listed[T](
    provider: str,
    rows: Iterable[T],
    make: Callable[[T], RemoteLink],
) -> list[RemoteLink]:
    found: list[RemoteLink] = []
    for row in rows:
        try:
            found.append(make(row))
        except ValidationError:
            logger.warning(
                "%s: a listed link was dropped: %s",
                provider,
                row,
                exc_info=True,
            )
    return found


_TURN_SCRIPT = """
local now = tonumber(ARGV[1])
local gap = tonumber(ARGV[2])
local nxt = tonumber(redis.call('GET', KEYS[1]) or '0')
local at = math.max(now, nxt)
redis.call('SET', KEYS[1], tostring(at + gap), 'PX', math.ceil(gap * 1000) + 60000)
return tostring(at - now)
"""

_UNAUTHORIZED = 401
_PAYMENT_REQUIRED = 402
_TOO_MANY = 429


class LinkProvider(ABC):
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
        return settings.links.admin_key(cls.key)

    @classmethod
    def local_key(cls, user_id: int) -> str | None:  # noqa: ARG003
        return None

    @abstractmethod
    def _headers(self) -> RequestHeaders: ...

    def _error(self, response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:  # silent: not JSON: the text is the error
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

    def _wait_turn(self, gap_s: float) -> None:
        if gap_s <= 0:
            return
        digest = hashlib.sha256(self.api_key.encode()).hexdigest()[:16]
        try:
            wait = float(
                get_redis().eval(
                    _TURN_SCRIPT,
                    1,
                    f"links:turn:{self.key}:{digest}",
                    time.time(),
                    gap_s,
                ),
            )
        except RedisError:
            logger.warning("link throttle: Redis unreachable", exc_info=True)
            wait = self._last_call + gap_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _call(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> requests.Response:
        info = self.info()
        self._wait_turn(info.min_interval_s)
        try:
            response = self._session.request(
                method,
                info.base_url + path,
                headers=self._headers().model_dump(by_alias=True),
                timeout=settings.links.request_timeout_s,
                json=json,
                params=params,
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
    def create(self, link: NewLink) -> RemoteLink: ...

    @abstractmethod
    def list_links(self) -> list[RemoteLink]: ...

    @abstractmethod
    def daily(
        self,
        remote_ids: list[str],
        start: date,
        end: date,
    ) -> list[LinkDays]: ...

    @abstractmethod
    def hourly(self, remote_ids: list[str], day: date) -> list[LinkHours]: ...

    @abstractmethod
    def enable_adult(self, remote_id: str) -> None: ...

    @abstractmethod
    def delete(self, remote_id: str) -> None: ...

    @abstractmethod
    def usage(self) -> Usage: ...
