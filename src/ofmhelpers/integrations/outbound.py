"""Outbound HTTP (requests) and its fakes switch.

Every `requests` call the app makes to an outside service goes through a
session `mount`ed here, or through `get` / `head`. Normally that changes
nothing. Under OFM_FAKE_EXTERNALS (`settings.infra.fake_externals`, e2e) every
such request is answered in-process by the fake of its host -- Instagram and
its CDN (`instagram/fake.py`), kie.ai (`integrations/ai_providers/kie/fake.py`),
Higgsfield's file host (`integrations/ai_providers/higgsfield/fake.py`), Google's and
Apple's sign-in token endpoints (`signin/fake.py`), Oopsie and Bouncy
(`links/fake.py`), Apify (`apify/fake.py`), Stacked (`stacked/fake.py`), Fanvue (`fanvue/fake.py`), RunningHub
(`integrations/ai_providers/runninghub/fake.py`), Elyum (`integrations/ai_providers/elyum/fake.py`), APIYI
(`integrations/ai_providers/apiyi/fake.py`),
Telegram (`integrations/senders/telegram/fake.py`) -- and a
host without a fake is refused with a connection error: nothing reaches the
internet. Higgsfield's API (httpx, its SDK) and Drive (googleapiclient)
are switched in their own clients.

A fake is a function (request, proxies) -> `FakeReply`; it may raise a
`requests` exception to stand for a dead proxy or a timeout.
"""

import io
import json
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

import requests
from pydantic import BaseModel, ConfigDict, Field
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.utils.registry import Registry

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


class FakeRoute(BaseModel):
    """One host and the fake answering it: what a fake's `routes()` lists."""

    model_config = ConfigDict(frozen=True)

    host: str
    handler: FakeHandler


@lru_cache(maxsize=1)
def _routes() -> Registry[FakeHandler]:
    """Host -> its fake. Imported on first use: the fakes import the
    clients' models, which import this module."""
    from ofmhelpers.integrations.ai_providers.apiyi.fake import routes as apiyi_routes
    from ofmhelpers.integrations.ai_providers.elyum.fake import routes as elyum_routes
    from ofmhelpers.integrations.ai_providers.higgsfield.fake import routes as hf_routes
    from ofmhelpers.integrations.ai_providers.kie.fake import routes as kie_routes
    from ofmhelpers.integrations.ai_providers.runninghub.fake import routes as rh_routes
    from ofmhelpers.integrations.apify.fake import routes as apify_routes
    from ofmhelpers.integrations.downloaders.savers_fake import routes as savers_routes
    from ofmhelpers.integrations.fanvue.fake import routes as fanvue_routes
    from ofmhelpers.integrations.instagram.fake import routes as instagram_routes
    from ofmhelpers.integrations.senders.telegram.fake import routes as telegram_routes
    from ofmhelpers.integrations.short_links.fake import routes as links_routes
    from ofmhelpers.integrations.signin.fake import routes as signin_routes
    from ofmhelpers.integrations.stacked.fake import routes as stacked_routes

    routes = [
        *instagram_routes(),
        *kie_routes(),
        *hf_routes(),
        *signin_routes(),
        *links_routes(),
        *savers_routes(),
        *apify_routes(),
        *stacked_routes(),
        *fanvue_routes(),
        *rh_routes(),
        *elyum_routes(),
        *apiyi_routes(),
        *telegram_routes(),
    ]
    return Registry[FakeHandler](entries={r.host: r.handler for r in routes})


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
