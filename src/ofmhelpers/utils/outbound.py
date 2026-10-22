"""Outbound HTTP (requests) and its fakes switch.

Every `requests` call the app makes to an outside service goes through a
session `mount`ed here, or through `get` / `head`. Normally that changes
nothing. Under OFM_FAKE_EXTERNALS (`settings.infra.fake_externals`, e2e) every
such request is answered in-process by the fake of its host -- Instagram and
its CDN (`instagram/fake.py`), kie.ai (`aigenproviders/kaiai/fake.py`),
Higgsfield's file host (`aigenproviders/higgsfield/fake.py`), Google's and
Apple's sign-in token endpoints (`signin/fake.py`), Oopsie and Bouncy
(`links/fake.py`) -- and a
host without a fake is refused with a connection error: nothing reaches the
internet. Higgsfield's API (httpx, its SDK) and Drive (googleapiclient)
are switched in their own clients.

A fake is a function (request, proxies) -> `FakeReply`; it may raise a
`requests` exception to stand for a dead proxy or a timeout.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

import requests
from pydantic import BaseModel, Field
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class FakeReply(BaseModel):
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def json_body(cls, data: Any, status: int = 200) -> FakeReply:
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


FakeHandler = Callable[[requests.PreparedRequest, Mapping[str, str]], FakeReply]


@lru_cache(maxsize=1)
def _routes() -> dict[str, FakeHandler]:
    """Host -> its fake. Imported on first use: the fakes import the
    clients' models, which import this module."""
    from ofmhelpers.aigenproviders.higgsfield.fake import routes as hf_routes
    from ofmhelpers.aigenproviders.kaiai.fake import routes as kie_routes
    from ofmhelpers.instagram.fake import routes as instagram_routes
    from ofmhelpers.links.fake import routes as links_routes
    from ofmhelpers.signin.fake import routes as signin_routes

    return {
        **instagram_routes(),
        **kie_routes(),
        **hf_routes(),
        **signin_routes(),
        **links_routes(),
    }


class FakeAdapter(BaseAdapter):
    """A transport answering from the fakes, never from the network."""

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,  # noqa: ARG002
        timeout: Any = None,  # noqa: ARG002
        verify: Any = True,  # noqa: ARG002
        cert: Any = None,  # noqa: ARG002
        proxies: Mapping[str, str] | None = None,
    ) -> requests.Response:
        host = urlsplit(request.url or "").hostname or ""
        handler = _routes().get(host)
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
    """`session`, answered by the fakes under OFM_FAKE_EXTERNALS (as is
    otherwise)."""
    if settings.infra.fake_externals:
        adapter = FakeAdapter()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    return session


def get(url: str, **kwargs: Any) -> requests.Response:
    """`requests.get`, through the fakes when they are on."""
    if settings.infra.fake_externals:
        return mount(requests.Session()).get(url, **kwargs)
    return requests.get(url, **kwargs)  # noqa: S113 -- the caller passes timeout


def post(url: str, **kwargs: Any) -> requests.Response:
    """`requests.post`, through the fakes when they are on."""
    if settings.infra.fake_externals:
        return mount(requests.Session()).post(url, **kwargs)
    return requests.post(url, **kwargs)  # noqa: S113 -- the caller passes timeout


def head(url: str, **kwargs: Any) -> requests.Response:
    """`requests.head`, through the fakes when they are on."""
    if settings.infra.fake_externals:
        return mount(requests.Session()).head(url, **kwargs)
    return requests.head(url, **kwargs)  # noqa: S113 -- the caller passes timeout
