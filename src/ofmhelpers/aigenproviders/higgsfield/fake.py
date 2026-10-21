"""Higgsfield faked at the wire (OFM_FAKE_EXTERNALS, tests): the official
SDK runs unchanged over an `httpx.MockTransport` answering its endpoints
(docs.higgsfield.ai, see this package's CLAUDE.md): the presigned upload
(`/files/generate-upload-url`, then a keyless PUT), the submit
(`POST /<model id>`) and `/requests/<id>/status`.

Only `FAKE_KEY` is accepted; any other key gets 401 (the app shows
"Higgsfield: wrong API key"). A request is "queued" for `polls_until_done`
status reads, then "completed" with one output on the fake result host
(also served to `requests`: the provider's `_live` check and the download).
"""

from __future__ import annotations

import itertools
import threading
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from ofmhelpers.config import settings
from ofmhelpers.utils import fake_media
from ofmhelpers.utils.outbound import FakeHandler, FakeReply

if TYPE_CHECKING:
    from collections.abc import Mapping

    import requests
    from higgsfield_client import SyncClient

FAKE_KEY = "fake-hf-id:fake-hf-secret"
FILES_HOST = "fake-files.higgsfield.ai"
_DENIED = {"detail": "Invalid credentials"}


class FakeHiggsfield:
    def __init__(self, polls_until_done: int = 2) -> None:
        self.polls_until_done = polls_until_done
        self.requests: dict[str, dict] = {}
        self.submitted: list[str] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def _answer(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == FILES_HOST:
            return httpx.Response(200)
        if request.headers.get("Authorization") != f"Key {FAKE_KEY}":
            return httpx.Response(401, json=_DENIED)
        base = settings.higgsfield.base_url.rstrip("/")
        path = url.path
        if path == "/files/generate-upload-url":
            number = next(self._ids)
            return httpx.Response(
                200,
                json={
                    "public_url": f"https://{FILES_HOST}/uploads/{number}",
                    "upload_url": f"https://{FILES_HOST}/put/{number}",
                },
            )
        if path.startswith("/requests/") and path.endswith("/status"):
            return self._status(path.split("/")[2])
        if request.method == "POST":
            model = path.lstrip("/")
            with self._lock:
                request_id = f"fake-hf-{next(self._ids):06d}"
                self.requests[request_id] = {"model": model, "polls": 0}
                self.submitted.append(request_id)
            return httpx.Response(
                200,
                json={
                    "status": "queued",
                    "request_id": request_id,
                    "status_url": f"{base}/requests/{request_id}/status",
                    "cancel_url": f"{base}/requests/{request_id}/cancel",
                },
            )
        return httpx.Response(404, json={"detail": "Not Found"})

    def _status(self, request_id: str) -> httpx.Response:
        found = self.requests.get(request_id)
        if found is None:
            return httpx.Response(404, json={"detail": "Request not found"})
        with self._lock:
            found["polls"] += 1
            done = found["polls"] > self.polls_until_done
        if not done:
            return httpx.Response(
                200, json={"status": "queued", "request_id": request_id}
            )
        video = "video" in found["model"]
        output = (
            {"video": {"url": f"https://{FILES_HOST}/results/{request_id}.mp4"}}
            if video
            else {"images": [{"url": f"https://{FILES_HOST}/results/{request_id}.png"}]}
        )
        return httpx.Response(
            200, json={"status": "completed", "request_id": request_id, **output}
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._answer)


@lru_cache(maxsize=1)
def fake_higgsfield() -> FakeHiggsfield:
    return FakeHiggsfield()


def install(client: SyncClient) -> None:
    """Points the SDK client's two httpx clients at the fake. They are the
    SDK's `cached_property`s `_client` / `_upload_client` (higgsfield-client
    0.2.0); the fake e2e and tests/contract fail loudly if a release renames
    them."""
    transport = fake_higgsfield().transport()
    client.__dict__["_client"] = httpx.Client(
        transport=transport,
        base_url=client.base_url,
        timeout=client.timeout,
        headers={
            "Authorization": f"Key {client.api_key}",
            "Content-Type": "application/json",
        },
    )
    client.__dict__["_upload_client"] = httpx.Client(
        transport=transport, timeout=client.timeout
    )


def _files(request: requests.PreparedRequest, _proxies: Mapping[str, str]) -> FakeReply:
    path = urlsplit(request.url or "").path
    if path.endswith(".mp4"):
        return FakeReply(body=fake_media.mp4(), headers={"Content-Type": "video/mp4"})
    return FakeReply(
        body=fake_media.image(fmt="PNG"), headers={"Content-Type": "image/png"}
    )


def routes() -> dict[str, FakeHandler]:
    return {FILES_HOST: _files}
