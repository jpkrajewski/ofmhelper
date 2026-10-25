"""Gemini faked at the wire (OFM_FAKE_EXTERNALS, tests): the official SDK
runs unchanged over an `httpx.MockTransport` (its `HttpOptions.httpx_client`)
answering the Gemini API endpoints it calls (ai.google.dev/api):

- `POST /v1beta/models/<model>:generateContent`: the answer is the next
  queued one (`answer_next`, `fail_next`), else the plainest value the
  request's `responseJsonSchema` allows (true, 0, "", [], an enum's first
  value) -- for a pass/fail checker that reads "passed".
- the Files API: the resumable upload (`POST /upload/v1beta/files` hands out
  an upload URL; chunks to it, the last "finalize"), `GET` (a file is
  PROCESSING for `polls_until_active` reads, then ACTIVE) and `DELETE`. A
  request naming a file that is missing or not ACTIVE is a 400, as Google's.

Only `FAKE_KEY` is accepted (`x-goog-api-key`); any other key gets Google's
400 "API key not valid"; a model not in `MODELS`, Google's 404. State is
per process (memory): the app under e2e and its tests never share it.
"""

from __future__ import annotations

import itertools
import json
import threading
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs

import httpx

from ofmhelpers.llm.fakes import example, field

FAKE_KEY = "fake-gemini-key"
HOST = "generativelanguage.googleapis.com"
# Free-tier text/vision models (ai.google.dev/gemini-api/docs/models, 2026-10-24).
MODELS = frozenset(
    {
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
    }
)
_BAD_KEY = (
    400,
    "INVALID_ARGUMENT",
    "API key not valid. Please pass a valid API key.",
)


def _error(
    code: int, status: str, message: str, details: list[dict] | None = None
) -> httpx.Response:
    body = {"error": {"code": code, "message": message, "status": status}}
    if details:
        body["error"]["details"] = details
    return httpx.Response(code, json=body)


class FakeGemini:
    def __init__(self, polls_until_active: int = 1) -> None:
        self.polls_until_active = polls_until_active
        # Every generateContent body, oldest first.
        self.requests: list[dict[str, Any]] = []
        self.files: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        # Uploaded files end FAILED instead of ACTIVE (`fail_processing`).
        self.processing_fails = False
        self._replies: list[httpx.Response | str] = []
        self._uploads: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # --- test hooks ---------------------------------------------------------

    def answer_next(self, answer: dict[str, Any] | str) -> None:
        """The next generateContent's answer text (a dict is sent as JSON)."""
        with self._lock:
            self._replies.append(
                answer if isinstance(answer, str) else json.dumps(answer)
            )

    def fail_next(
        self,
        code: int,
        status: str,
        message: str,
        retry_delay_s: float | None = None,
    ) -> None:
        details = (
            [
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": f"{retry_delay_s:g}s",
                }
            ]
            if retry_delay_s is not None
            else None
        )
        with self._lock:
            self._replies.append(_error(code, status, message, details))

    def refuse_next(self) -> None:
        """The next generateContent is blocked by the safety filters."""
        with self._lock:
            self._replies.append(
                httpx.Response(
                    200,
                    json={
                        "promptFeedback": {"blockReason": "PROHIBITED_CONTENT"},
                        "candidates": [],
                    },
                )
            )

    def fail_processing(self) -> None:
        """Every file uploaded from now on fails Google's processing."""
        self.processing_fails = True

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._answer)

    # --- the wire -----------------------------------------------------------

    def _answer(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host != HOST:
            return _error(404, "NOT_FOUND", f"Unknown host {url.host}")
        path = url.path
        if path.startswith("/upload/") and "upload_id" in parse_qs(url.query.decode()):
            return self._chunk(request)
        if request.headers.get("x-goog-api-key") != FAKE_KEY:
            return _error(*_BAD_KEY)
        if path.endswith(":generateContent") and request.method == "POST":
            model = path.rsplit("/", 1)[-1].removesuffix(":generateContent")
            if model not in MODELS:
                return _error(
                    404,
                    "NOT_FOUND",
                    f"models/{model} is not found for API version v1beta, or is"
                    " not supported for generateContent.",
                )
            return self._generate(json.loads(request.content or b"{}"))
        if path.startswith("/upload/") and request.method == "POST":
            return self._start_upload(json.loads(request.content or b"{}"))
        if path.startswith("/v1beta/files/"):
            name = path.removeprefix("/v1beta/")
            if request.method == "DELETE":
                return self._delete(name)
            return self._get(name)
        return _error(404, "NOT_FOUND", f"No such method {request.method} {path}")

    def _generate(self, body: dict[str, Any]) -> httpx.Response:
        with self._lock:
            self.requests.append(body)
            queued = self._replies.pop(0) if self._replies else None
        if isinstance(queued, httpx.Response):
            return queued
        missing = self._missing_file(body)
        if missing:
            return _error(400, "FAILED_PRECONDITION", missing)
        if queued is None:
            config = body.get("generationConfig", {})
            schema = config.get("responseJsonSchema") or config.get("responseSchema")
            queued = json.dumps(example(schema) if schema else {})
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": queued}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 1, "totalTokenCount": 2},
            },
        )

    def _missing_file(self, body: dict[str, Any]) -> str | None:
        for content in body.get("contents", []):
            for part in content.get("parts", []):
                data = field(part, "fileData", "file_data") or {}
                uri = field(data, "fileUri", "file_uri")
                if uri is None:
                    continue
                name = uri.split("/v1beta/", 1)[-1]
                found = self.files.get(name)
                if found is None or found["state"] != "ACTIVE":
                    return f"The File {name} is not in an ACTIVE state"
        return None

    def _start_upload(self, body: dict[str, Any]) -> httpx.Response:
        upload_id = str(next(self._ids))
        with self._lock:
            self._uploads[upload_id] = {"meta": body.get("file", {}), "size": 0}
        upload_url = (
            f"https://{HOST}/upload/v1beta/files?upload_id={upload_id}"
            "&upload_protocol=resumable"
        )
        return httpx.Response(200, json={}, headers={"x-goog-upload-url": upload_url})

    def _chunk(self, request: httpx.Request) -> httpx.Response:
        upload_id = parse_qs(request.url.query.decode())["upload_id"][0]
        with self._lock:
            upload = self._uploads.get(upload_id)
            if upload is None:
                return _error(404, "NOT_FOUND", "No such upload")
            upload["size"] += len(request.content)
            if "finalize" not in request.headers.get("X-Goog-Upload-Command", ""):
                return httpx.Response(200, headers={"x-goog-upload-status": "active"})
            name = f"files/fake-{upload_id}"
            meta = upload["meta"]
            self.files[name] = {
                "name": name,
                "uri": f"https://{HOST}/v1beta/{name}",
                "mimeType": field(meta, "mimeType", "mime_type")
                or "application/octet-stream",
                "sizeBytes": str(upload["size"]),
                "state": "PROCESSING" if self.polls_until_active else "ACTIVE",
                "polls": 0,
            }
            made = self._public(self.files[name])
        return httpx.Response(
            200, json={"file": made}, headers={"x-goog-upload-status": "final"}
        )

    def _get(self, name: str) -> httpx.Response:
        with self._lock:
            found = self.files.get(name)
            if found is None:
                return _error(404, "NOT_FOUND", f"File {name} not found")
            found["polls"] += 1
            if found["polls"] >= self.polls_until_active:
                found["state"] = "FAILED" if self.processing_fails else "ACTIVE"
            return httpx.Response(200, json=self._public(found))

    def _delete(self, name: str) -> httpx.Response:
        with self._lock:
            if self.files.pop(name, None) is None:
                return _error(404, "NOT_FOUND", f"File {name} not found")
            self.deleted.append(name)
        return httpx.Response(200, json={})

    @staticmethod
    def _public(file: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in file.items() if k != "polls"}


@lru_cache(maxsize=1)
def fake_gemini() -> FakeGemini:
    return FakeGemini()
