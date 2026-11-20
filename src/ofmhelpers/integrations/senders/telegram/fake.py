import json
import threading
import time
from functools import lru_cache
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit
lazy from collections.abc import Mapping

from pydantic import BaseModel
lazy import requests

from ofmhelpers.integrations.outbound import FakeReply, FakeRoute
from ofmhelpers.integrations.senders.telegram.models import MAX_TEXT

HOST = "api.telegram.org"
TOKEN = "123456789:AAFakeTelegramBotTokenForTheTestsX0"  # noqa: S105
BOT = {"id": 123456789, "is_bot": True, "username": "ofm_fake_bot", "first_name": "OFM"}


class SentMessage(BaseModel):
    chat_id: str
    text: str


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[SentMessage] = []
        self.deleted: list[SentMessage] = []
        self._by_id: dict[tuple[str, int], SentMessage] = {}
        self.fail_next: list[FakeReply] = []
        self._updates: list[dict[str, Any]] = []
        self._last_id = 0
        self._lock = threading.Lock()

    def wrote(
        self,
        chat_id: int,
        first_name: str = "Owner",
        text: str = "/start",
        sent_at: float | None = None,
    ) -> None:
        kind = "group" if chat_id < 0 else "private"
        with self._lock:
            self._last_id += 1
            self._updates.append(
                {
                    "update_id": self._last_id,
                    "message": {
                        "message_id": self._last_id,
                        "chat": {"id": chat_id, "type": kind, "first_name": first_name},
                        "text": text,
                        "date": int(time.time() if sent_at is None else sent_at),
                    },
                }
            )

    def odd_update(self) -> None:
        with self._lock:
            self._last_id += 1
            self._updates.append(
                {"update_id": self._last_id, "message": {"text": "money"}}
            )

    def _updates_from(self, body: dict[str, Any]) -> FakeReply:
        offset = body.get("offset")
        with self._lock:
            if offset is not None:
                self._updates = [u for u in self._updates if u["update_id"] >= offset]
            found = list(self._updates)
        return _ok(found)

    def _delete(self, body: dict[str, Any]) -> FakeReply:
        with self._lock:
            found = self._by_id.pop(
                (str(body["chat_id"]), int(body["message_id"])), None
            )
            if found is not None:
                self.deleted.append(found)
        return _delete_reply(found)

    def handle(self, request: requests.PreparedRequest) -> FakeReply:
        if self.fail_next:
            return self.fail_next.pop(0)
        bot, _, method = urlsplit(request.url or "").path.strip("/").partition("/")
        if bot != f"bot{TOKEN}":
            return error(HTTPStatus.UNAUTHORIZED, "Unauthorized")
        raw = request.body if isinstance(request.body, bytes | str) else ""
        body = json.loads(raw or "{}")
        if method == "getMe":
            return _ok(BOT)
        if method == "getUpdates":
            return self._updates_from(body)
        if method == "sendMessage":
            return self._send(body)
        if method == "deleteMessage":
            return self._delete(body)
        return error(HTTPStatus.NOT_FOUND, "Not Found")

    def _send(self, body: dict[str, Any]) -> FakeReply:
        text = str(body.get("text", ""))
        if not text:
            return error(HTTPStatus.BAD_REQUEST, "Bad Request: message text is empty")
        if len(text.encode("utf-16-le")) // 2 > MAX_TEXT:
            return error(HTTPStatus.BAD_REQUEST, "Bad Request: message is too long")
        chat_id = str(body["chat_id"])
        with self._lock:
            self._last_id += 1
            sent = SentMessage(chat_id=chat_id, text=text)
            self.sent.append(sent)
            message_id = self._last_id
            self._by_id[(chat_id, message_id)] = sent
        return _ok(
            {
                "message_id": message_id,
                "chat": _chat(chat_id).model_dump(exclude_none=True),
                "text": text,
            }
        )


def _delete_reply(found: SentMessage | None) -> FakeReply:
    if found is None:
        return error(HTTPStatus.BAD_REQUEST, "Bad Request: message to delete not found")
    return _ok(result=True)


class FakeChat(BaseModel):
    id: int
    type: str
    username: str | None = None


def _chat(chat_id: str) -> FakeChat:
    if chat_id.startswith("@"):
        return FakeChat(id=-1001, type="channel", username=chat_id[1:])
    kind = "group" if chat_id.startswith("-") else "private"
    return FakeChat(id=int(chat_id), type=kind)


def _ok(result: Any) -> FakeReply:
    return FakeReply.json_body({"ok": True, "result": result})


def error(status: HTTPStatus, description: str) -> FakeReply:
    reply = {"ok": False, "error_code": status.value, "description": description}
    return FakeReply.json_body(reply, status.value)


def flood(retry_after: int) -> FakeReply:
    reply = {
        "ok": False,
        "error_code": HTTPStatus.TOO_MANY_REQUESTS.value,
        "description": f"Too Many Requests: retry after {retry_after}",
        "parameters": {"retry_after": retry_after},
    }
    return FakeReply.json_body(reply, HTTPStatus.TOO_MANY_REQUESTS.value)


def moved(chat_id: int) -> FakeReply:
    reply = {
        "ok": False,
        "error_code": HTTPStatus.BAD_REQUEST.value,
        "description": "Bad Request: group chat was upgraded to a supergroup chat",
        "parameters": {"migrate_to_chat_id": chat_id},
    }
    return FakeReply.json_body(reply, HTTPStatus.BAD_REQUEST.value)


@lru_cache(maxsize=1)
def fake_telegram() -> FakeTelegram:
    return FakeTelegram()


def routes() -> list[FakeRoute]:
    def telegram(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_telegram().handle(request)

    return [FakeRoute(host=HOST, handler=telegram)]
