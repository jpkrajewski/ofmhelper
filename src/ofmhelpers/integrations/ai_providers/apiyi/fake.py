# Copyright (C) 2026 jpkrajewski

import itertools
import json
import threading
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit
lazy from collections.abc import Mapping

import requests

from ofmhelpers.integrations.ai_providers.apiyi.fake_families import FakeFamilies
from ofmhelpers.integrations.outbound import FakeReply, FakeRoute
from ofmhelpers.media import fake_media

FAKE_KEY = "sk-fakeApiyiKey0123456789abcdefghijklmnopqrstuv"
HOST = "api.apiyi.com"
FILES_HOST = "fake-oss.apiyi.com"
_SUBMIT = "/wan/api/v1/services/aigc/video-generation/video-synthesis"
_TASKS = "/v1/tasks/"
_MODELS = "/v1/models"
_FRAMES = frozenset({"first_frame", "last_frame"})
_REFERENCES = frozenset({"reference_image", "reference_video", "reference_audio"})
_RESOLUTIONS = frozenset({"480P", "720P", "1080P"})


def error(status: int, code: str, message: str) -> FakeReply:
    return FakeReply.json_body(
        {"error": {"message": message, "type": "new_api_error", "code": code}},
        status=status,
    )


def _said(code: str, message: str) -> str:
    return f"[{code}] {message}"


def _problem(body: dict[str, Any]) -> str | None:
    media = body.get("input", {}).get("media") or []
    types = {m.get("type") for m in media}
    if types & _FRAMES and types & _REFERENCES:
        return _said("InvalidParameter", "first_frame cannot be mixed with reference_*")
    for item in media:
        if not str(item.get("url", "")).startswith("https://"):
            return _said("InvalidURL", f"url not reachable: {item.get('url')}")
    parameters = body.get("parameters", {})
    if not isinstance(parameters.get("duration", 5), int):
        return _said("InvalidParameter", "duration must be an integer")
    if parameters.get("resolution", "1080P") not in _RESOLUTIONS:
        return _said("InvalidParameter", "resolution must be 480P, 720P or 1080P")
    return None


class FakeApiyi:
    def __init__(self, polls_until_done: int = 2) -> None:
        self.polls_until_done = polls_until_done
        self.tasks: dict[str, dict[str, Any]] = {}
        self.created: list[dict[str, Any]] = []
        self.refuse_next: tuple[int, str, str] | None = None
        self.fail_next: tuple[str, str] | None = None
        self.refuse_reads: list[tuple[int, str, str]] = []
        self.lose_next = False
        self.model_lists = 0
        self.key_models: dict[str, list[str]] = {}
        self.families = FakeFamilies(polls_until_done)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def handle(self, request: requests.PreparedRequest) -> FakeReply:
        key = str(request.headers.get("Authorization", "")).removeprefix("Bearer ")
        if key != FAKE_KEY and key not in self.key_models:
            return error(401, "invalid_api_key", "无效的令牌 (invalid token)")
        path = urlsplit(request.url or "").path
        if request.method == "GET" and path == _MODELS:
            with self._lock:
                self.model_lists += 1
            listed = self.key_models.get(key, ["wan3.0-video", "wan3.0-video-prime"])
            return FakeReply.json_body(
                {"object": "list", "data": [{"id": model} for model in listed]},
            )
        other = self.families.handle(request, path)
        if other is not None:
            return other
        if request.method == "POST" and path == _SUBMIT:
            if request.headers.get("X-DashScope-Async") != "enable":
                return FakeReply.json_body(
                    {
                        "code": "AccessDenied",
                        "message": "current user api does "
                        "not support synchronous calls",
                    },
                    status=403,
                )
            raw = request.body
            body = json.loads(raw if isinstance(raw, (str, bytes)) and raw else b"{}")
            return self._submit(body)
        if request.method == "GET" and path.startswith(_TASKS):
            return self._task(path.removeprefix(_TASKS))
        return error(404, "not_found", f"No route {request.method} {path}")

    def _submit(self, body: dict[str, Any]) -> FakeReply:
        with self._lock:
            refused, self.refuse_next = self.refuse_next, None
            self.created.append(body)
            if refused:
                return error(*refused)
            task_id = f"00000000-0000-4000-8000-{next(self._ids):012d}"
            failure, self.fail_next = self.fail_next, None
            self.tasks[task_id] = {
                "polls": 0,
                "failure": _said(*failure) if failure else _problem(body),
            }
            lost, self.lose_next = self.lose_next, False
        if lost:
            msg = "fake: the answer was lost"
            raise requests.ReadTimeout(msg)
        return FakeReply.json_body(
            {
                "output": {"task_id": task_id, "task_status": "PENDING"},
                "request_id": f"req-{task_id[-6:]}",
            },
        )

    def _task(self, task_id: str) -> FakeReply:
        with self._lock:
            if self.refuse_reads:
                return error(*self.refuse_reads.pop(0))
            found = self.tasks.get(task_id)
            if found is None:
                return error(404, "task_not_found", f"task {task_id} not found")
            found["polls"] += 1
            polls = found["polls"]
        if polls <= self.polls_until_done:
            state = "submitted" if polls == 1 else "in_progress"
            return self._read(task_id, state, progress="30%")
        if found["failure"]:
            said = found["failure"]
            return self._read(
                task_id,
                "failed",
                progress="100%",
                error={"message": said, "type": "task_failed"},
                fail_reason=said,
            )
        url = (
            f"https://{FILES_HOST}/output/{task_id}.mp4"
            "?Expires=1793907937&OSSAccessKeyId=fake&Signature=fake"
        )
        return self._read(task_id, "completed", progress="100%", result_url=url)

    @staticmethod
    def _read(
        task_id: str,
        state: str,
        progress: str | None = None,
        result_url: str | None = None,
        error: dict[str, str] | None = None,
        fail_reason: str | None = None,
    ) -> FakeReply:
        body: dict[str, Any] = {
            "id": task_id,
            "status": state,
            "result_url": result_url,
            "error": error,
        }
        if progress is not None:
            body["progress"] = progress
        if fail_reason is not None:
            body["fail_reason"] = fail_reason
        return FakeReply.json_body(body)


@lru_cache(maxsize=1)
def fake_apiyi() -> FakeApiyi:
    return FakeApiyi()


def _files(request: requests.PreparedRequest, _proxies: Mapping[str, str]) -> FakeReply:
    if request.headers.get("Authorization"):
        return FakeReply(body=b"AccessDenied", status=403)
    if urlsplit(request.url or "").path.endswith(".png"):
        return FakeReply(
            body=fake_media.image(fmt="PNG"),
            headers={"Content-Type": "image/png"},
        )
    return FakeReply(body=fake_media.mp4(), headers={"Content-Type": "video/mp4"})


def routes() -> list[FakeRoute]:
    def api(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_apiyi().handle(request)

    return [
        FakeRoute(host=HOST, handler=api),
        FakeRoute(host=FILES_HOST, handler=_files),
    ]
