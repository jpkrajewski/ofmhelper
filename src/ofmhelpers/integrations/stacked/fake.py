# Copyright (C) 2026 jpkrajewski

import json
import threading
from datetime import datetime
from functools import lru_cache
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
lazy from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field
lazy import requests

from ofmhelpers.integrations.outbound import FakeReply, FakeRoute

HOST = "api.stacked.com"
_PREFIX = "/external-service/v1/"
CREATOR = "user_fake_creator"
OTHER_CREATOR = "user_fake_other"
AGENCY = "agency_fake"
CREATOR_KEY = "stk_live_fake_creator"
AGENCY_KEY = "stk_live_fake_agency"
_START_MS = 1_790_000_000_000
_MAX_PAGE = 100
_DEFAULT_PAGE = 50


class FakeCreator(BaseModel):
    user_id: str = Field(serialization_alias="userId")
    username: str
    access_types: list[str] = Field(serialization_alias="accessTypes")


def _creator(user_id: str, access: str) -> FakeCreator:
    return FakeCreator(
        user_id=user_id,
        username=user_id.removeprefix("user_"),
        access_types=[access],
    )


class FakeMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_id: int = Field(serialization_alias="messageId")
    user_id: str = Field(serialization_alias="userId")
    created_at: int = Field(serialization_alias="createdAt")
    type: str
    custom_type: str = Field(serialization_alias="customType")
    text: str


class FakeStacked:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.chats: dict[str, list[dict[str, Any]]] = {}
        self.webhooks: dict[str, list[dict[str, Any]]] = {}
        self.fail_next: list[FakeReply] = []
        self.sales: dict[str, list[tuple[float, int]]] = {}
        self.subscribers: dict[str, list[tuple[float, int]]] = {}
        self._messages: dict[str, list[dict[str, Any]]] = {}
        self._last_id = 1000
        self._lock = threading.Lock()

    def add_chat(self, creator: str, fan: str, channel_url: str) -> None:
        members = [
            {
                "userId": user_id,
                "nickname": user_id,
                "isOnline": False,
                "lastSeenAt": 0,
                "isBlockedByCreator": False,
                "isBlockingCreator": False,
            }
            for user_id in (creator, fan)
        ]
        self.chats.setdefault(creator, []).append(
            {
                "channelUrl": channel_url,
                "name": f"{creator} & {fan}",
                "customType": "DIRECT",
                "createdAt": _START_MS // 1000,
                "memberUserIds": [creator, fan],
                "members": members,
                "messageCount": 0,
                "unreadMessageCount": 0,
            },
        )
        self._messages[channel_url] = []

    def add_message(
        self,
        channel_url: str,
        user_id: str,
        text: str,
        at_ms: int | None = None,
    ) -> FakeMessage:
        self._last_id += 1
        made = FakeMessage(
            message_id=self._last_id,
            user_id=user_id,
            created_at=at_ms or _START_MS + self._last_id,
            type="MESG" if user_id else "ADMM",
            custom_type="" if user_id else "SENDBIRD:AUTO_EVENT_MESSAGE",
            text=text,
        )
        message = made.model_dump(by_alias=True)
        self._messages[channel_url].insert(0, message)
        chat = next(
            c
            for chats in self.chats.values()
            for c in chats
            if c["channelUrl"] == channel_url
        )
        chat["messageCount"] += 1
        chat["lastMessage"] = dict(message)
        return made

    def add_reaction(
        self,
        channel_url: str,
        message_id: int,
        user_id: str,
        at_ms: int,
    ) -> None:
        message = next(
            m for m in self._messages[channel_url] if m["messageId"] == message_id
        )
        message.setdefault("reactions", []).append(
            {"key": "fire", "userIds": [user_id], "updatedAt": at_ms},
        )

    def sold(self, creator: str, gross_cents: int, at: float) -> None:
        self.sales.setdefault(creator, []).append((at, gross_cents))

    def subscribed(self, creator: str, at: float) -> None:
        self.subscribers.setdefault(creator, []).append((at, 1))

    def handle(self, request: requests.PreparedRequest) -> FakeReply:
        url = urlsplit(request.url or "")
        with self._lock:
            self.calls.append((request.method or "", url.path))
            failure = self.fail_next.pop(0) if self.fail_next else None
        if failure is not None:
            return failure
        token = str(request.headers.get("Authorization") or "").removeprefix("Bearer ")
        reachable = _REACHABLE.get(token)
        if reachable is None:
            return _error(HTTPStatus.UNAUTHORIZED, "invalid API key")
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        parts = url.path.removeprefix(_PREFIX).split("/")
        if parts == ["me"]:
            return FakeReply.json_body(_me(token).model_dump())
        if parts == ["creators"]:
            access = "owner" if token == CREATOR_KEY else "agency"
            return FakeReply.json_body(
                {
                    "creators": [
                        _creator(c, access).model_dump(by_alias=True) for c in reachable
                    ],
                },
            )
        creator = query.get("creatorUserId")
        if not creator:
            return _error(
                HTTPStatus.BAD_REQUEST,
                "creatorUserId is required "
                "(query param or X-Stacked-Creator-User-Id header)",
            )
        if creator not in reachable:
            return _error(
                HTTPStatus.FORBIDDEN,
                "api key does not have access to this creator",
            )
        if parts == ["chats"]:
            return self._chats(creator, query)
        if parts == ["earnings"]:
            return self._earnings(creator, query)
        if parts == ["subscribers", "summary"]:
            return self._subscribers(creator, query)
        if parts[0] == "webhooks":
            return self._webhooks(request, creator, parts)
        if len(parts) == 3 and parts[0] == "chats" and parts[2] == "messages":  # noqa: PLR2004
            return self._chat_messages(creator, unquote(parts[1]), query)
        return _error(HTTPStatus.NOT_FOUND, "not found")

    def _in_window(
        self,
        items: list[tuple[float, int]],
        query: Mapping[str, str],
    ) -> list[int]:
        start = _seconds(query["startTime"])
        end = _seconds(query["endTime"])
        return [value for at, value in items if start <= at < end]

    def _earnings(self, creator: str, query: Mapping[str, str]) -> FakeReply:
        gross = sum(self._in_window(self.sales.get(creator, []), query))
        summary = {
            "creatorUserId": creator,
            "currency": "usd",
            "grossUnitAmount": gross,
            "netUnitAmount": gross * 9 // 10,
            "windowStart": query["startTime"],
            "windowEnd": query["endTime"],
            "series": [],
        }
        return FakeReply.json_body({"earnings": [summary]})

    def _subscribers(self, creator: str, query: Mapping[str, str]) -> FakeReply:
        new = len(self._in_window(self.subscribers.get(creator, []), query))
        summary = {
            "creatorUserId": creator,
            "activeSubscribers": new,
            "newSubscribers": new,
            "canceledSubscribers": 0,
            "resubscribers": 0,
            "windowStart": query["startTime"],
            "windowEnd": query["endTime"],
            "series": [],
        }
        return FakeReply.json_body({"summaries": [summary]})

    def _webhooks(
        self,
        request: requests.PreparedRequest,
        creator: str,
        parts: list[str],
    ) -> FakeReply:
        token = str(request.headers.get("Authorization") or "").removeprefix("Bearer ")
        held = creator if token == CREATOR_KEY else f"{token}:{creator}"
        mine = self.webhooks.setdefault(held, [])
        if request.method == "GET":
            return FakeReply.json_body({"webhooks": mine})
        if request.method == "DELETE":
            mine[:] = [w for w in mine if w["id"] != parts[1]]
            return FakeReply.json_body({})
        body = request.body if isinstance(request.body, bytes | str) else "{}"
        sent = json.loads(body or "{}")
        self._last_id += 1
        made = {
            "id": f"wh_{self._last_id}",
            "url": sent["url"],
            "events": sent["events"],
        }
        mine.append(made)
        return FakeReply.json_body({**made, "secret": f"whsec_{self._last_id}"})

    def _chats(self, creator: str, query: Mapping[str, str]) -> FakeReply:
        chats = sorted(
            self.chats.get(creator, []),
            key=lambda c: c.get("lastMessage", {}).get("createdAt", 0),
            reverse=True,
        )
        start = int(query.get("cursor", 0))
        end = start + min(int(query.get("limit", _DEFAULT_PAGE)), _MAX_PAGE)
        body: dict[str, Any] = {"chats": chats[start:end]}
        if end < len(chats):
            body["nextCursor"] = str(end)
        return FakeReply.json_body(body)

    def _chat_messages(
        self,
        creator: str,
        channel_url: str,
        query: Mapping[str, str],
    ) -> FakeReply:
        if not any(c["channelUrl"] == channel_url for c in self.chats.get(creator, [])):
            return _error(HTTPStatus.NOT_FOUND, "failed to fetch chat")
        messages = self._messages[channel_url]
        before = query.get("beforeMessageId")
        if before:
            messages = [m for m in messages if m["messageId"] < int(before)]
        limit = min(int(query.get("limit", _DEFAULT_PAGE)), _MAX_PAGE)
        return FakeReply.json_body({"messages": messages[:limit]})


_REACHABLE = {
    CREATOR_KEY: [CREATOR],
    AGENCY_KEY: [CREATOR, OTHER_CREATOR],
}


def _seconds(rfc3339: str) -> float:
    return datetime.fromisoformat(rfc3339).timestamp()


class FakeAccount(BaseModel):
    type: str
    id: str


class FakeMe(BaseModel):
    account: FakeAccount
    name: str = "Fake key"
    scopes: list[str] = ["chat:read"]


def _me(token: str) -> FakeMe:
    account = (
        FakeAccount(type="creator", id=CREATOR)
        if token == CREATOR_KEY
        else FakeAccount(type="agency", id=AGENCY)
    )
    return FakeMe(account=account)


def _error(status: HTTPStatus, message: str) -> FakeReply:
    return FakeReply.json_body({"error": message}, status)


@lru_cache(maxsize=1)
def fake_stacked() -> FakeStacked:
    return FakeStacked()


def routes() -> list[FakeRoute]:
    def stacked(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_stacked().handle(request)

    return [FakeRoute(host=HOST, handler=stacked)]
