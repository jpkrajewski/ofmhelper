"""The fake's half for APIYI's other families (`fake.py` dispatches here):
Gemini `generateContent`, the OpenAI images API, Seedance's Ark tasks and
Veo's `/v1/videos`, answered the way docs.apiyi.com describes them, with the
traps the docs warn about:

- Seedance: without `Accept-Encoding: identity` the gateway's mislabelled
  gzip body arrives truncated (an unreadable answer).
- Veo: `seconds` as a number is refused (400 parse_request_failed);
  `generateAudio` is refused; `/content` needs the key (the API host
  checks it for every route); `veo_url` False = a completed task without a
  `video_url`.
- The images API: the official gpt-image-2 refuses `response_format`.
"""

import base64
import itertools
import json
import re
import threading
from typing import Any

lazy import requests

from ofmhelpers.media import fake_media
from ofmhelpers.utils.outbound import FakeReply

FILES_HOST = "fake-oss.apiyi.com"
_PART = re.compile(
    rb'Content-Disposition: form-data; name="([^"]+)"(?:; filename="([^"]+)")?'
    rb"\r\n(?:Content-Type: [^\r\n]+\r\n)?\r\n(.*?)\r\n--",
    re.DOTALL,
)


def error(status: int, code: str, message: str) -> FakeReply:
    return FakeReply.json_body(
        {"error": {"message": message, "type": "shell_api_error", "code": code}},
        status=status,
    )


def form(raw: bytes) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """A multipart body's plain fields and its (field, file name) files."""
    fields: dict[str, str] = {}
    files: list[tuple[str, str]] = []
    for name, filename, value in _PART.findall(raw):
        if filename:
            files.append((name.decode(), filename.decode()))
        else:
            fields[name.decode()] = value.decode(errors="replace")
    return fields, files


def _png_b64() -> str:
    return base64.b64encode(fake_media.image(fmt="PNG")).decode()


class FakeFamilies:
    def __init__(self, polls_until_done: int = 2) -> None:
        self.polls_until_done = polls_until_done
        # Every submit: (path, JSON body or multipart fields, file names).
        self.calls: list[tuple[str, dict[str, Any], list[tuple[str, str]]]] = []
        # The next generateContent answers this body instead of an image.
        self.gemini_next: dict[str, Any] | None = None
        self.veo_url = True
        self.tasks: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def handle(self, request: requests.PreparedRequest, path: str) -> FakeReply | None:
        """The answer when `path` is one of these families', else None."""
        method = request.method
        if method == "POST" and path.endswith(":generateContent"):
            return self._gemini(request, path)
        if method == "POST" and path in ("/v1/images/generations", "/v1/images/edits"):
            return self._images(request, path)
        if path.startswith("/seedance/api/v3/contents/generations/tasks"):
            return self._seedance(request, path)
        if path.startswith("/v1/videos"):
            return self._veo(request, path)
        return None

    def _record(
        self,
        path: str,
        body: dict[str, Any],
        files: list[tuple[str, str]] | None = None,
    ) -> None:
        with self._lock:
            self.calls.append((path, body, files or []))

    @staticmethod
    def _json(request: requests.PreparedRequest) -> dict[str, Any]:
        raw = request.body
        loaded = json.loads(raw if isinstance(raw, (str, bytes)) and raw else b"{}")
        return loaded if isinstance(loaded, dict) else {}

    # --- Gemini -----------------------------------------------------------

    def _gemini(self, request: requests.PreparedRequest, path: str) -> FakeReply:
        body = self._json(request)
        self._record(path, body)
        with self._lock:
            answer, self.gemini_next = self.gemini_next, None
        if answer is not None:
            return FakeReply.json_body(answer)
        draft = {"inlineData": {"mimeType": "image/png", "data": _png_b64()}}
        final = {"inlineData": {"mimeType": "image/png", "data": _png_b64()}}
        return FakeReply.json_body(
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [draft, {"text": "x"}, final],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10},
            }
        )

    # --- OpenAI images ----------------------------------------------------

    def _images(self, request: requests.PreparedRequest, path: str) -> FakeReply:
        content_type = str(request.headers.get("Content-Type", ""))
        if content_type.startswith("multipart/"):
            raw = request.body if isinstance(request.body, bytes) else b""
            body, files = form(raw)
            self._record(path, body, files)
        else:
            body = self._json(request)
            self._record(path, body)
        model = str(body.get("model", ""))
        if not model:
            return error(400, "invalid_request_error", "model is required")
        if model == "gpt-image-2" and "response_format" in body:
            return error(
                400, "unknown_parameter", "Unknown parameter: 'response_format'."
            )
        if (
            model.startswith(("seedream", "flux"))
            or body.get("response_format") == "url"
        ):
            url = f"https://{FILES_HOST}/images/{next(self._ids)}.png"
            return FakeReply.json_body({"created": 1, "data": [{"url": url}]})
        return FakeReply.json_body({"created": 1, "data": [{"b64_json": _png_b64()}]})

    # --- Seedance ---------------------------------------------------------

    def _seedance(self, request: requests.PreparedRequest, path: str) -> FakeReply:
        if request.headers.get("Accept-Encoding") != "identity":
            # The gateway labels a plain body gzip: the client reads garbage.
            return FakeReply(
                body=b'id":"cgt-trunc', headers={"Content-Type": "application/json"}
            )
        if request.method == "POST":
            body = self._json(request)
            self._record(path, body)
            task_id = f"cgt-2026110600000{next(self._ids)}-fake"
            with self._lock:
                self.tasks[task_id] = {"polls": 0}
            return FakeReply.json_body({"id": task_id})
        task_id = path.rsplit("/", 1)[-1]
        polls = self._poll(task_id)
        if polls is None:
            return error(404, "NotFound", f"task {task_id} not found")
        if polls <= self.polls_until_done:
            return FakeReply.json_body({"id": task_id, "status": "running"})
        url = f"https://{FILES_HOST}/seedance/{task_id}.mp4?X-Tos-Expires=86400"
        return FakeReply.json_body(
            {
                "id": task_id,
                "status": "succeeded",
                "content": {"video_url": url},
                "usage": {"completion_tokens": 108900},
            }
        )

    # --- Veo --------------------------------------------------------------

    def _veo(self, request: requests.PreparedRequest, path: str) -> FakeReply:
        if request.method == "POST" and path == "/v1/videos":
            return self._veo_submit(request, path)
        rest = path.removeprefix("/v1/videos/")
        task_id, _, tail = rest.partition("/")
        if tail == "content":
            return FakeReply(
                body=fake_media.mp4(), headers={"Content-Type": "video/mp4"}
            )
        polls = self._poll(task_id)
        if polls is None:
            return error(404, "not_found", f"task {task_id} not found")
        if polls <= self.polls_until_done:
            return FakeReply.json_body({"id": task_id, "status": "in_progress"})
        done: dict[str, Any] = {"id": task_id, "status": "completed", "progress": 100}
        if self.veo_url:
            done["video_url"] = f"https://{FILES_HOST}/veo/{task_id}_0.mp4"
        return FakeReply.json_body(done)

    def _veo_submit(self, request: requests.PreparedRequest, path: str) -> FakeReply:
        content_type = str(request.headers.get("Content-Type", ""))
        if content_type.startswith("multipart/"):
            raw = request.body if isinstance(request.body, bytes) else b""
            body, files = form(raw)
            self._record(path, body, files)
            seconds: Any = body.get("seconds", "8")
        else:
            body = self._json(request)
            self._record(path, body)
            files = []
            seconds = body.get("seconds", "8")
            if "generateAudio" in json.dumps(body):
                return error(400, "INVALID_ARGUMENT", "generateAudio is not supported")
        if not isinstance(seconds, str):
            return error(
                400,
                "parse_request_failed",
                "cannot unmarshal number into Go struct field of type string",
            )
        task_id = f"task_fake{next(self._ids)}"
        with self._lock:
            self.tasks[task_id] = {"polls": 0}
        return FakeReply.json_body(
            {"id": task_id, "task_id": task_id, "object": "video", "status": "queued"}
        )

    def _poll(self, task_id: str) -> int | None:
        with self._lock:
            found = self.tasks.get(task_id)
            if found is None:
                return None
            found["polls"] += 1
            return int(found["polls"])
