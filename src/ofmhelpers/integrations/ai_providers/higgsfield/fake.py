import itertools
import threading
from functools import lru_cache
from urllib.parse import urlsplit
lazy from collections.abc import Mapping

import httpx
lazy import requests
lazy from higgsfield_client import SyncClient

from ofmhelpers.config import settings
from ofmhelpers.integrations.outbound import FakeReply, FakeRoute
from ofmhelpers.media import fake_media

FAKE_KEY = "fake-hf-id:fake-hf-secret"
FILES_HOST = "fake-files.higgsfield.ai"
_DENIED = {"detail": "Invalid credentials"}


class FakeHiggsfield:
    def __init__(self, polls_until_done: int = 2) -> None:
        self.polls_until_done = polls_until_done
        self.requests: dict[str, dict] = {}
        self.submitted: list[str] = []
        self.keys: dict[str, str] = {}
        self.lose_next = False
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
            key = request.headers.get("Idempotency-Key")
            with self._lock:
                request_id = self.keys.get(key) if key else None
                if request_id is None:
                    request_id = f"fake-hf-{next(self._ids):06d}"
                    self.requests[request_id] = {"model": model, "polls": 0}
                    self.submitted.append(request_id)
                if key:
                    self.keys[key] = request_id
                lost, self.lose_next = self.lose_next, False
            if lost:
                msg = "fake: the answer was lost"
                raise httpx.ReadTimeout(msg, request=request)
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
    transport = fake_higgsfield().transport()
    client._client = httpx.Client(  # noqa: SLF001
        transport=transport,
        base_url=client.base_url,
        timeout=client.timeout,
        headers={
            "Authorization": f"Key {client.api_key}",
            "Content-Type": "application/json",
        },
    )
    client._upload_client = httpx.Client(  # noqa: SLF001
        transport=transport, timeout=client.timeout
    )


def _files(request: requests.PreparedRequest, _proxies: Mapping[str, str]) -> FakeReply:
    path = urlsplit(request.url or "").path
    if path.endswith(".mp4"):
        return FakeReply(body=fake_media.mp4(), headers={"Content-Type": "video/mp4"})
    return FakeReply(
        body=fake_media.image(fmt="PNG"), headers={"Content-Type": "image/png"}
    )


def routes() -> list[FakeRoute]:
    return [FakeRoute(host=FILES_HOST, handler=_files)]
