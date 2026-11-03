"""Elyum faked at the wire (OFM_FAKE_EXTERNALS, tests): `POST /v1/files`,
`POST /v1/generations` and `GET /v1/generations/{id}`, answered the way
Elyum documents them (elyum.ai/open/docs: real HTTP statuses, the error
envelope), plus the file host its output URLs point at.

Only `FAKE_KEY` is accepted; any other key gets `401 auth`. A generation is
queued/running for `polls_until_done` reads, then `succeeded` with one
output: an mp4 for a video model of the catalog, else a png. A repeated
`Idempotency-Key` returns the generation already made (no second one).
`refuse_next` makes the next create answer that (HTTP status, code,
message) instead (402 insufficient_balance, 429 concurrency ...),
`fail_next` the next generation end `failed` with that (code, message);
`created` records each (body, Idempotency-Key), `uploads` each uploaded
file name.
"""

from __future__ import annotations

import itertools
import json
import re
import threading
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ofmhelpers.media import fake_media
from ofmhelpers.utils.outbound import FakeHandler, FakeReply

if TYPE_CHECKING:
    from collections.abc import Mapping

    import requests

FAKE_KEY = "ek_live_fake-elyum-key"
HOST = "api.elyum.ai"
FILES_HOST = "fake-files.elyum.ai"
_FILES = "/v1/files"
_GENERATIONS = "/v1/generations"
_FILENAME = re.compile(rb'filename="([^"]+)"')


def error(status: int, code: str, message: str) -> FakeReply:
    return FakeReply.json_body(
        {
            "error": {
                "code": code,
                "message": message,
                "param": None,
                "fix": None,
                "retryable": status in (429, 503),
                "retry_after": None,
                "request_id": "req_fake",
                "docs_url": "https://elyum.ai/open/docs#error-codes",
            }
        },
        status=status,
    )


class FakeElyum:
    def __init__(self, polls_until_done: int = 2) -> None:
        self.polls_until_done = polls_until_done
        self.generations: dict[str, dict[str, Any]] = {}
        self.by_key: dict[str, str] = {}
        self.created: list[tuple[dict[str, Any], str]] = []
        self.refuse_next: tuple[int, str, str] | None = None
        self.fail_next: tuple[str, str] | None = None
        self.uploads: list[str] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def handle(self, request: requests.PreparedRequest) -> FakeReply:
        if request.headers.get("Authorization") != f"Bearer {FAKE_KEY}":
            return error(
                401, "auth", "Send an API key: Authorization: Bearer ek_live_…"
            )
        path = urlsplit(request.url or "").path
        if request.method == "POST" and path == _FILES:
            return self._upload(request)
        if request.method == "POST" and path == _GENERATIONS:
            raw = request.body
            body = json.loads(raw if isinstance(raw, (str, bytes)) and raw else b"{}")
            return self._create(body, str(request.headers.get("Idempotency-Key", "")))
        if request.method == "GET" and path.startswith(f"{_GENERATIONS}/"):
            return self._get(path.removeprefix(f"{_GENERATIONS}/"))
        return error(404, "not_found", f"No route {request.method} {path}")

    def _upload(self, request: requests.PreparedRequest) -> FakeReply:
        raw = request.body if isinstance(request.body, bytes) else b""
        found = _FILENAME.search(raw)
        name = found.group(1).decode() if found else "upload"
        with self._lock:
            number = next(self._ids)
            self.uploads.append(name)
        return FakeReply.json_body(
            {
                "id": f"file_{number:06d}",
                "object": "file",
                "type": "video"
                if name.endswith((".mp4", ".mov", ".webm"))
                else "image",
                "bytes": len(raw),
                "name": name,
            },
            status=201,
        )

    def _create(self, body: dict[str, Any], key: str) -> FakeReply:
        with self._lock:
            if key and key in self.by_key:
                found = self.generations[self.by_key[key]]
                reply = self._generation(found["id"], "queued")
                return reply.model_copy(
                    update={"headers": {**reply.headers, "Idempotent-Replayed": "true"}}
                )
            refused, self.refuse_next = self.refuse_next, None
            self.created.append((body, key))
            if refused:
                return error(*refused)
            generation_id = f"g_{next(self._ids):06d}"
            failure, self.fail_next = self.fail_next, None
            self.generations[generation_id] = {
                "id": generation_id,
                "model": str(body.get("model", "")),
                "polls": 0,
                "failure": failure,
            }
            if key:
                self.by_key[key] = generation_id
        return self._generation(generation_id, "queued", status=202)

    def _get(self, generation_id: str) -> FakeReply:
        with self._lock:
            found = self.generations.get(generation_id)
            if found is None:
                return error(404, "not_found", f"No generation {generation_id}")
            found["polls"] += 1
            done = found["polls"] > self.polls_until_done
        if not done:
            return self._generation(generation_id, "running")
        if found["failure"]:
            code, message = found["failure"]
            return self._generation(
                generation_id, "failed", error={"code": code, "message": message}
            )
        suffix = "mp4" if _is_video(found["model"]) else "png"
        output = {
            "index": 0,
            "url": f"https://{FILES_HOST}/output/{generation_id}.{suffix}",
            "type": "video" if suffix == "mp4" else "image",
        }
        return self._generation(
            generation_id,
            "succeeded",
            outputs=[output],
            usage={"usd": 1.58, "billed": "charged"},
        )

    @staticmethod
    def _generation(
        generation_id: str, state: str, status: int = 200, **fields: Any
    ) -> FakeReply:
        return FakeReply.json_body(
            {
                "id": generation_id,
                "object": "generation",
                "status": state,
                "delivery": "final",
                "outputs": [],
                "usage": None,
                "error": None,
                **fields,
            },
            status=status,
        )


def _is_video(model: str) -> bool:
    from ofmhelpers.integrations.ai_providers.catalog import catalog

    spec = catalog().get(f"elyum:{model}")
    return spec is not None and spec.kind == "video"


@lru_cache(maxsize=1)
def fake_elyum() -> FakeElyum:
    return FakeElyum()


def _files(request: requests.PreparedRequest, _proxies: Mapping[str, str]) -> FakeReply:
    if urlsplit(request.url or "").path.endswith(".mp4"):
        return FakeReply(body=fake_media.mp4(), headers={"Content-Type": "video/mp4"})
    return FakeReply(
        body=fake_media.image(fmt="PNG"), headers={"Content-Type": "image/png"}
    )


def routes() -> dict[str, FakeHandler]:
    def api(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_elyum().handle(request)

    return {HOST: api, FILES_HOST: _files}
