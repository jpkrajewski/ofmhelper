# Copyright (C) 2026 jpkrajewski

import io
import json
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit
lazy from collections.abc import Mapping

import requests
from pydantic import BaseModel, ConfigDict, Field
from requests.adapters import BaseAdapter, HTTPAdapter
from requests.structures import CaseInsensitiveDict
from urllib3.util.retry import Retry

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
lazy from ofmhelpers.config.settings import ProfilePageSettings

logger = get_logger(__name__)


class FakeReply(BaseModel):
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def json_body(
        cls,
        data: Mapping[str, Any] | list[Any],
        status: int = 200,
    ) -> FakeReply:
        return cls(
            status=status,
            body=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
        )

    @classmethod
    def text(cls, text: str, status: int = 200) -> FakeReply:
        return cls(
            status=status,
            body=text.encode(),
            headers={"Content-Type": "text/html; charset=utf-8"},
        )


@runtime_checkable
class FakeHandler(Protocol):
    def __call__(
        self,
        request: requests.PreparedRequest,
        proxies: Mapping[str, str],
        /,
    ) -> FakeReply: ...


class FakeRoute(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    host: str
    handler: FakeHandler


class _Fakes(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    handlers: dict[str, FakeHandler] = Field(default_factory=dict)


@lru_cache(maxsize=1)
def _fakes() -> _Fakes:
    return _Fakes()


def register_fakes(routes: list[FakeRoute]) -> None:
    _fakes().handlers.update({route.host: route.handler for route in routes})


class FakeAdapter(BaseAdapter):
    def send(  # noqa: PLR0913, PLR0917
        self,
        request: requests.PreparedRequest,
        stream: bool = False,  # noqa: ARG002, FBT001, FBT002
        timeout: float | tuple[float | None, float | None] | None = None,  # noqa: ARG002
        verify: bool | str = True,  # noqa: ARG002, FBT001, FBT002
        cert: str | tuple[str, str] | None = None,  # noqa: ARG002
        proxies: Mapping[str, str] | None = None,
    ) -> requests.Response:
        host = urlsplit(request.url or "").hostname or ""
        handler = _fakes().handlers.get(host)
        if handler is None:
            msg = f"OFM_FAKE_EXTERNALS: no fake for {host!r}, request refused"
            logger.warning("%s (%s %s)", msg, request.method, request.url)
            raise requests.ConnectionError(msg, request=request)
        reply = handler(request, proxies or {})
        response = requests.Response()
        response.status_code = reply.status
        response.headers = CaseInsensitiveDict(reply.headers)
        response.raw = io.BytesIO(reply.body)
        response.url = request.url or ""
        response.request = request
        response.reason = "OK" if reply.status < 400 else "Fake error"  # noqa: PLR2004
        response.encoding = "utf-8"
        return response

    def close(self) -> None:
        pass


def mount(session: requests.Session) -> requests.Session:
    if settings.infra.fake_externals:
        adapter = FakeAdapter()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    return session


_RETRY_STATUSES = (500, 502, 503, 504)


def page_session(page: ProfilePageSettings, proxy: str | None) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=page.http_retries,
        backoff_factor=page.retry_backoff_s,
        backoff_jitter=page.retry_backoff_s,
        status_forcelist=_RETRY_STATUSES,
        allowed_methods=None,
        raise_on_status=False,
        respect_retry_after_header=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": page.user_agent,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return mount(session)


def get(
    url: str,
    *,
    timeout: float | None = None,
    headers: Mapping[str, str] | None = None,
    stream: bool = False,
    allow_redirects: bool = True,
) -> requests.Response:
    if settings.infra.fake_externals:
        return mount(requests.Session()).get(
            url,
            timeout=timeout,
            headers=headers,
            stream=stream,
            allow_redirects=allow_redirects,
        )
    return requests.get(
        url,
        timeout=timeout,
        headers=headers,
        stream=stream,
        allow_redirects=allow_redirects,
    )


def post(
    url: str,
    *,
    timeout: float | None = None,
    headers: Mapping[str, str] | None = None,
    data: Mapping[str, str] | None = None,
) -> requests.Response:
    if settings.infra.fake_externals:
        return mount(requests.Session()).post(
            url,
            timeout=timeout,
            headers=headers,
            data=data,
        )
    return requests.post(url, timeout=timeout, headers=headers, data=data)


def head(
    url: str,
    *,
    timeout: float | None = None,
    allow_redirects: bool = False,
) -> requests.Response:
    if settings.infra.fake_externals:
        return mount(requests.Session()).head(
            url,
            timeout=timeout,
            allow_redirects=allow_redirects,
        )
    return requests.head(url, timeout=timeout, allow_redirects=allow_redirects)


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
