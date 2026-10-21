"""kie.ai faked at the wire (OFM_FAKE_EXTERNALS, tests): the file-stream
upload, createTask, recordInfo and the result download, answered the way
kie.ai does (HTTP 200, the outcome in the body's `code`), so the real
`KieAIClient` and provider run unchanged.

Only `FAKE_KEY` is accepted; any other key gets kie.ai's code 401 (the app
shows "kie.ai: wrong API key"). A task reports "generating" for
`polls_until_done` reads, then "success" with one result URL on the fake
result host. Nothing is paid, nothing leaves the process.
"""

from __future__ import annotations

import itertools
import json
import threading
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from ofmhelpers.config import settings
from ofmhelpers.utils import fake_media
from ofmhelpers.utils.outbound import FakeHandler, FakeReply

if TYPE_CHECKING:
    from collections.abc import Mapping

    import requests

FAKE_KEY = "fake-kie-key"
RESULT_HOST = "fake-results.kie.ai"
_DENIED = {"code": 401, "msg": "You do not have access permissions"}


class FakeKie:
    def __init__(self, polls_until_done: int = 2) -> None:
        self.polls_until_done = polls_until_done
        # task id -> {"model", "polls"}; createTask calls in order.
        self.tasks: dict[str, dict] = {}
        self.created: list[str] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    @staticmethod
    def _authorised(request: requests.PreparedRequest) -> bool:
        return request.headers.get("Authorization") == f"Bearer {FAKE_KEY}"

    def upload(self, request: requests.PreparedRequest) -> FakeReply:
        if not self._authorised(request):
            return FakeReply.json_body({"success": False, **_DENIED})
        number = next(self._ids)
        return FakeReply.json_body(
            {
                "success": True,
                "code": 200,
                "msg": "File uploaded successfully",
                "data": {"downloadUrl": f"https://{RESULT_HOST}/uploads/{number}"},
            }
        )

    def create(self, request: requests.PreparedRequest) -> FakeReply:
        if not self._authorised(request):
            return FakeReply.json_body(_DENIED)
        body = request.body if isinstance(request.body, (str, bytes)) else b"{}"
        model = json.loads(body or b"{}").get("model", "")
        with self._lock:
            task_id = f"fake-task-{next(self._ids):06d}"
            self.tasks[task_id] = {"model": model, "polls": 0}
            self.created.append(task_id)
        return FakeReply.json_body(
            {"code": 200, "msg": "success", "data": {"taskId": task_id}}
        )

    def record(self, request: requests.PreparedRequest) -> FakeReply:
        if not self._authorised(request):
            return FakeReply.json_body(_DENIED)
        task_id = parse_qs(urlsplit(request.url or "").query).get("taskId", [""])[0]
        task = self.tasks.get(task_id)
        if task is None:
            return FakeReply.json_body({"code": 422, "msg": "recordInfo is null"})
        with self._lock:
            task["polls"] += 1
            done = task["polls"] > self.polls_until_done
        data = {"taskId": task_id, "model": task["model"], "state": "generating"}
        if done:
            ext = "mp4" if _kind(task["model"]) == "video" else "png"
            data |= {
                "state": "success",
                "resultJson": json.dumps(
                    {"resultUrls": [f"https://{RESULT_HOST}/results/{task_id}.{ext}"]}
                ),
                "creditsConsumed": 12.5,
                "costTime": 1200,
            }
        return FakeReply.json_body({"code": 200, "msg": "success", "data": data})

    def handle_api(
        self, request: requests.PreparedRequest, _proxies: Mapping[str, str]
    ) -> FakeReply:
        path = urlsplit(request.url or "").path
        if path.endswith("/createTask") and request.method == "POST":
            return self.create(request)
        if path.endswith("/recordInfo"):
            return self.record(request)
        if path.endswith("/api/file-stream-upload") and request.method == "POST":
            return self.upload(request)
        return FakeReply.json_body({"code": 404, "msg": "Not Found"}, status=404)

    @staticmethod
    def handle_files(
        request: requests.PreparedRequest, _proxies: Mapping[str, str]
    ) -> FakeReply:
        path = urlsplit(request.url or "").path
        if path.endswith(".mp4"):
            return FakeReply(
                body=fake_media.mp4(), headers={"Content-Type": "video/mp4"}
            )
        return FakeReply(
            body=fake_media.image(fmt="PNG"), headers={"Content-Type": "image/png"}
        )


def _kind(model: str) -> str:
    from ofmhelpers.aigenproviders.catalog import catalog

    spec = catalog().get(f"kie:{model}")
    return spec.kind if spec else "image"


@lru_cache(maxsize=1)
def fake_kie() -> FakeKie:
    return FakeKie()


def routes() -> dict[str, FakeHandler]:
    s = settings.kieai
    api = lambda request, proxies: fake_kie().handle_api(request, proxies)  # noqa: E731
    hosts = {urlsplit(s.jobs_base).hostname, urlsplit(s.upload_base).hostname}
    return {
        **{host: api for host in hosts if host},
        RESULT_HOST: FakeKie.handle_files,
    }
