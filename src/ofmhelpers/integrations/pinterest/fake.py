# Copyright (C) 2026 jpkrajewski

import json
import re
import threading
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, urlsplit
lazy from collections.abc import Mapping

from pydantic import BaseModel
from pydantic_core import to_jsonable_python
lazy import requests

from ofmhelpers.integrations.outbound import FakeReply, FakeRoute

HOST = "www.pinterest.com"
UPLOAD_HOST = "pinterest-media-upload.s3.amazonaws.com"


class FakeAccount(BaseModel):
    id: str
    username: str


class FakeBoard(BaseModel):
    id: str
    name: str
    privacy: str = "public"


class FakeSlot(BaseModel):
    upload_id: str
    upload_url: str
    upload_parameters: dict[str, str]


class FakeUpload(BaseModel):
    upload_id: str
    status: str


class FakeUploadFailed(FakeUpload):
    failure_code: int


class FakeUploadSucceeded(FakeUpload):
    signature: str
    failure_code: None = None


class FakePinMade(BaseModel):
    id: str
    node_id: str


ACCOUNT = FakeAccount(id="722053890000000001", username="fake_pinner")
BOARDS = [
    FakeBoard(id="722053821446932302", name="Daily"),
    FakeBoard(id="722053821447306957", name="Reels"),
]


class FakePin(BaseModel):
    id: str
    body: dict[str, Any]


class FakePinterest:
    def __init__(self) -> None:
        self.pins: list[FakePin] = []
        self.uploaded: list[str] = []
        self.logged_out = False
        self.broken_uploads = False
        self.account = ACCOUNT.model_copy()
        self.boards = [b.model_copy() for b in BOARDS]
        self.fail_next: list[FakeReply] = []
        self._uploads: dict[str, int] = {}
        self._next = 0
        self._lock = threading.Lock()

    def _id(self) -> str:
        self._next += 1
        return str(4044292523484093440 + self._next)

    def answer(self, request: requests.PreparedRequest) -> FakeReply:
        parts = urlsplit(request.url or "")
        if parts.hostname == UPLOAD_HOST:
            return self._put(request)
        cookies = str(request.headers.get("Cookie", ""))
        if self.logged_out or "_pinterest_sess=" not in cookies:
            return FakeReply.text("Invalid Resource Request", status=403)
        with self._lock:
            if self.fail_next:
                return self.fail_next.pop(0)
        if parts.path.endswith("/get/"):
            raw = parse_qs(parts.query).get("data", ["{}"])[0]
        else:
            body = request.body.decode() if isinstance(request.body, bytes) else ""
            raw = parse_qs(body or str(request.body or "")).get("data", ["{}"])[0]
        session = re.search(r"_pinterest_sess=([^;]+)", cookies)
        self.account = ACCOUNT.model_copy(
            update={"username": f"pin_{session.group(1) if session else 'x'}"},
        )
        options = json.loads(raw).get("options", {})
        if "/BoardResource/create/" in parts.path:
            return _ok(self._board(options["name"]))
        return self._resource(options.get("url", ""), options.get("data", {}))

    def _board(self, name: str) -> FakeBoard:
        with self._lock:
            board = FakeBoard(id=self._id(), name=name)
            self.boards.append(board)
            return board

    def _resource(self, path: str, data: dict[str, Any]) -> FakeReply:
        with self._lock:
            if path == "/v3/users/me/":
                return _ok(self.account)
            if path == "/v3/users/me/boards/":
                return _ok(self.boards)
            if path == "/v3/media/uploads/register/batch/":
                found: dict[str, FakeSlot] = {}
                for info in json.loads(data["media_info_list"]):
                    upload_id = self._id()
                    self._uploads[upload_id] = 0
                    found[info["id"]] = FakeSlot(
                        upload_id=upload_id,
                        upload_url=f"https://{UPLOAD_HOST}/",
                        upload_parameters={"key": upload_id, "policy": "fake"},
                    )
                return _ok(found)
            if path == "/v3/media/uploads/":
                return _ok({u: self._state(u) for u in data["upload_ids"]})
            if path == "/v3/storypins/":
                pin = FakePin(id=self._id(), body=data)
                self.pins.append(pin)
                return _ok(FakePinMade(id=pin.id, node_id="fake"))
        return _ok(None)

    def _state(self, upload_id: str) -> FakeUpload:
        asked = self._uploads.get(upload_id)
        if asked is None or upload_id not in self.uploaded:
            return FakeUpload(upload_id=upload_id, status="registered")
        self._uploads[upload_id] = asked + 1
        if self.broken_uploads:
            return FakeUploadFailed(
                upload_id=upload_id,
                status="failed",
                failure_code=3,
            )
        if asked == 0:
            return FakeUpload(upload_id=upload_id, status="processing")
        return FakeUploadSucceeded(
            upload_id=upload_id,
            status="succeeded",
            signature=f"sig{upload_id}",
        )

    def _put(self, request: requests.PreparedRequest) -> FakeReply:
        body = request.body if isinstance(request.body, bytes) else b""
        for upload_id in self._uploads:
            if f'name="key"\r\n\r\n{upload_id}\r\n'.encode() in body:
                with self._lock:
                    self.uploaded.append(upload_id)
                return FakeReply(status=204)
        return FakeReply.text("no such slot", status=403)


def _ok(data: object) -> FakeReply:
    return FakeReply.json_body(
        {
            "resource_response": {
                "status": "success",
                "code": 0,
                "data": to_jsonable_python(data),
            },
        },
    )


@lru_cache(maxsize=1)
def fake_pinterest() -> FakePinterest:
    return FakePinterest()


def _handle(
    request: requests.PreparedRequest,
    _proxies: Mapping[str, str],
) -> FakeReply:
    return fake_pinterest().answer(request)


def routes() -> list[FakeRoute]:
    return [
        FakeRoute(host=HOST, handler=_handle),
        FakeRoute(host=UPLOAD_HOST, handler=_handle),
    ]
