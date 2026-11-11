"""RunningHub faked at the wire (OFM_FAKE_EXTERNALS, tests): the Standard
Model API's upload (`/openapi/v2/media/upload/binary`), any model submit
(`POST /openapi/v2/<model path>`) and `/openapi/v2/query`, answered the way
RunningHub does (HTTP 200, the code in the body), plus the file host its
upload and result URLs point at.

Only `FAKE_KEY` is accepted; any other key gets 806 APIKEY_USER_NOT_FOUND
(an upload: code 1 "ApiKey verification failed", as the real one answers).
A task is QUEUED/RUNNING for `polls_until_done` queries, then SUCCESS with
one result: an mp4 for a video model of the catalog, else a png.
`refuse_next` makes the next submit answer that (code, message) instead
(no balance, concurrency, content policy), `fail_next` the next task end
FAILED with it; `submitted` records each (model path, body), `uploads`
each uploaded file name.
"""

import itertools
import json
import threading
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit
lazy from collections.abc import Mapping

lazy import requests

from ofmhelpers.integrations.outbound import FakeReply, FakeRoute
from ofmhelpers.media import fake_media

FAKE_KEY = "fake-runninghub-key"
HOST = "www.runninghub.ai"
FILES_HOST = "fake-files.runninghub.ai"
_API = "/openapi/v2/"


def _task(task_id: str = "", status: str = "", **fields: Any) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "status": status,
        "errorCode": "",
        "errorMessage": "",
        "results": None,
        "clientId": "",
        "promptTips": "",
        "failedReason": {},
        "usage": None,
        **fields,
    }


def _refused(code: int, message: str, task_id: str = "") -> FakeReply:
    """A refusal echoes the queried `taskId`, as the real API does."""
    return FakeReply.json_body(
        _task(task_id, errorCode=str(code), errorMessage=message)
    )


class FakeRunningHub:
    def __init__(self, polls_until_done: int = 2) -> None:
        self.polls_until_done = polls_until_done
        self.tasks: dict[str, dict[str, Any]] = {}
        self.submitted: list[tuple[str, dict[str, Any]]] = []
        self.refuse_next: tuple[int, str] | None = None
        self.fail_next: tuple[int, str] | None = None
        self.uploads: list[str] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def handle(self, request: requests.PreparedRequest) -> FakeReply:
        path = urlsplit(request.url or "").path
        authorized = request.headers.get("Authorization") == f"Bearer {FAKE_KEY}"
        if not authorized and path == f"{_API}media/upload/binary":
            return FakeReply.json_body(
                {"code": 1, "msg": "ApiKey verification failed: API Key not found"}
            )
        if not authorized:
            raw = request.body
            body = json.loads(raw if isinstance(raw, (str, bytes)) and raw else b"{}")
            task_id = str(body.get("taskId", "")) if path == f"{_API}query" else ""
            return _refused(806, "APIKEY_USER_NOT_FOUND", task_id)
        if path == f"{_API}media/upload/binary":
            number = next(self._ids)
            with self._lock:
                self.uploads.append(str(number))
            return FakeReply.json_body(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "type": "image",
                        "download_url": f"https://{FILES_HOST}/input/{number}?q-sign=x",
                        "filename": f"openapi/{number}",
                    },
                }
            )
        raw = request.body
        body = json.loads(raw if isinstance(raw, (str, bytes)) and raw else b"{}")
        if path == f"{_API}query":
            return self._query(str(body.get("taskId", "")))
        return self._submit(path.removeprefix(_API), body)

    def _submit(self, model: str, body: dict[str, Any]) -> FakeReply:
        with self._lock:
            refused, self.refuse_next = self.refuse_next, None
            self.submitted.append((model, body))
            if refused:
                return _refused(*refused)
            task_id = f"20{next(self._ids):017d}"
            failure, self.fail_next = self.fail_next, None
            self.tasks[task_id] = {"model": model, "polls": 0, "failure": failure}
        return FakeReply.json_body(_task(task_id, "QUEUED"))

    def _query(self, task_id: str) -> FakeReply:
        with self._lock:
            found = self.tasks.get(task_id)
            if found is None:
                return _refused(807, "APIKEY_TASK_NOT_FOUND", task_id)
            found["polls"] += 1
            done = found["polls"] > self.polls_until_done
        if not done:
            return FakeReply.json_body(_task(task_id, "RUNNING"))
        if found["failure"]:
            code, message = found["failure"]
            return FakeReply.json_body(
                _task(task_id, "FAILED", errorCode=str(code), errorMessage=message)
            )
        suffix = "mp4" if _is_video(found["model"]) else "png"
        result = {"url": f"https://{FILES_HOST}/output/{task_id}.{suffix}"}
        usage = {"consumeMoney": "0.42", "consumeCoins": "0", "taskCostTime": "12"}
        return FakeReply.json_body(
            _task(
                task_id,
                "SUCCESS",
                results=[result | {"outputType": suffix}],
                usage=usage,
            )
        )


def _is_video(model: str) -> bool:
    from ofmhelpers.integrations.ai_providers.catalog import catalog

    spec = catalog().get(f"runninghub:{model}")
    return spec is not None and spec.kind == "video"


@lru_cache(maxsize=1)
def fake_runninghub() -> FakeRunningHub:
    return FakeRunningHub()


def _files(request: requests.PreparedRequest, _proxies: Mapping[str, str]) -> FakeReply:
    if urlsplit(request.url or "").path.endswith(".mp4"):
        return FakeReply(body=fake_media.mp4(), headers={"Content-Type": "video/mp4"})
    return FakeReply(
        body=fake_media.image(fmt="PNG"), headers={"Content-Type": "image/png"}
    )


def routes() -> list[FakeRoute]:
    def api(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_runninghub().handle(request)

    return [
        FakeRoute(host=HOST, handler=api),
        FakeRoute(host=FILES_HOST, handler=_files),
    ]
