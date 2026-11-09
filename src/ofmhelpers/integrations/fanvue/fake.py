"""Fanvue's API and token endpoint faked at the wire (OFM_FAKE_EXTERNALS, tests).

Answers the calls `client.FanvueClient` and `oauth` make the way Fanvue does (real
answers checked 2026-11-05, see CLAUDE.md). One agency, `AGENCY`, managing `CREATOR`
and `OTHER_CREATOR`. `add_message` writes in a chat. Refresh tokens rotate, and
presenting a spent one kills the authorisation, like the real thing. An agency's token
gets 403 on webhook subscriptions, as seen. `fail_next` answers the next API calls with
an error, `token_failures` the next token calls; `calls` records (method, path, query).
"""

import threading
from datetime import UTC, datetime
from functools import lru_cache
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, urlsplit
lazy from collections.abc import Mapping

lazy import requests

from ofmhelpers.utils.outbound import FakeHandler, FakeReply

API_HOST = "api.fanvue.com"
AUTH_HOST = "auth.fanvue.com"
AGENCY = "agency-fake-uuid"
AGENCY_HANDLE = "fake_agency"
CREATOR = "creator-fake-uuid"
CREATOR_HANDLE = "fake_creator"
OTHER_CREATOR = "creator-other-uuid"
OTHER_HANDLE = "fake_other"
GOOD_CODE = "fake-login-code"
_PREFIX = "/v1/"
_CHAT_PATH_LENGTH = 3
_MESSAGES_PATH_LENGTH = 5
_EXPIRES_IN_S = 3600
_START_S = 1_790_000_000


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


class FakeFanvue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.fail_next: list[FakeReply] = []
        self.token_failures: list[FakeReply] = []
        self.refreshes = 0
        self.authorisation_dead = False
        # {creator: {fan: messages, newest first}}
        self._chats: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._tokens_made = 0
        self._access: set[str] = set()
        self._refresh: str | None = None
        self._last_id = 0
        self._lock = threading.Lock()

    # --- test data ----------------------------------------------------------

    def add_message(
        self,
        creator: str,
        fan: str,
        *,
        from_fan: bool,
        at: float | None = None,
        text: str = "hi",
        message_type: str = "SINGLE_RECIPIENT",
        status: str = "SENT",
    ) -> str:
        """Someone writes in the chat of `creator` and `fan`, which starts it."""
        self._last_id += 1
        uuid = f"fake-message-{self._last_id}"
        self._chats.setdefault(creator, {}).setdefault(fan, []).insert(
            0,
            {
                "uuid": uuid,
                "text": text,
                "sentAt": _iso(at if at is not None else _START_S + self._last_id),
                "sender": {"uuid": fan if from_fan else creator},
                "type": message_type,
                "status": status,
            },
        )
        return uuid

    def new_tokens(self) -> dict[str, Any]:
        """A fresh pair; the previous refresh token is spent."""
        self._tokens_made += 1
        access = f"fake-access-{self._tokens_made}"
        refresh = f"fake-refresh-{self._tokens_made}"
        self._access.add(access)
        self._refresh = refresh
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": _EXPIRES_IN_S,
            "token_type": "bearer",
        }

    # --- the token endpoint -------------------------------------------------

    def handle_auth(self, request: requests.PreparedRequest) -> FakeReply:
        with self._lock:
            if self.token_failures:
                return self.token_failures.pop(0)
            if not str(request.headers.get("Authorization") or "").startswith("Basic "):
                return FakeReply.json_body(
                    {"error": "invalid_client"}, HTTPStatus.UNAUTHORIZED
                )
            raw = request.body or ""
            text = raw.decode() if isinstance(raw, bytes) else str(raw)
            form = {k: v[0] for k, v in parse_qs(text).items()}
            if form.get("grant_type") == "authorization_code":
                if form.get("code") != GOOD_CODE or not form.get("code_verifier"):
                    return _invalid_grant("The authorization code is invalid")
                return FakeReply.json_body(self.new_tokens())
            self.refreshes += 1
            if self.authorisation_dead or form.get("refresh_token") != self._refresh:
                self.authorisation_dead = True
                self._access.clear()
                return _invalid_grant("The refresh chain is broken")
            return FakeReply.json_body(self.new_tokens())

    # --- the API ------------------------------------------------------------

    def handle(self, request: requests.PreparedRequest) -> FakeReply:
        url = urlsplit(request.url or "")
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        with self._lock:
            self.calls.append((request.method or "", url.path, query))
            failure = self.fail_next.pop(0) if self.fail_next else None
        if failure is not None:
            return failure
        token = str(request.headers.get("Authorization") or "").removeprefix("Bearer ")
        if token not in self._access:
            return FakeReply.json_body(
                {"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED
            )
        parts = url.path.removeprefix(_PREFIX).split("/")
        if parts == ["users", "me"]:
            return FakeReply.json_body(
                {"uuid": AGENCY, "handle": AGENCY_HANDLE, "roles": ["agency"]}
            )
        if parts == ["creators"]:
            return FakeReply.json_body({"data": _MANAGED, "nextCursor": None})
        if parts[:2] == ["webhooks", "subscriptions"]:
            return FakeReply.json_body({"error": "Bad Request"}, HTTPStatus.FORBIDDEN)
        if parts[0] == "creators" and len(parts) >= _CHAT_PATH_LENGTH:
            if len(parts) == _CHAT_PATH_LENGTH and parts[2] == "chats":
                return self._chat_list(parts[1], query)
            if len(parts) == _MESSAGES_PATH_LENGTH and parts[4] == "messages":
                return self._messages(parts[1], parts[3], query)
        return FakeReply.json_body({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _chat_list(self, creator: str, query: Mapping[str, str]) -> FakeReply:
        """Latest activity first; the cursor is an offset, opaque to the client."""
        chats = sorted(
            self._chats.get(creator, {}).items(),
            key=lambda chat: chat[1][0]["sentAt"],
            reverse=True,
        )
        start = int(query.get("cursor") or 0)
        end = start + int(query.get("size", 50))
        return FakeReply.json_body(
            {
                "data": [
                    {
                        "user": {"uuid": fan, "handle": fan, "displayName": fan},
                        "lastMessage": {
                            "uuid": messages[0]["uuid"],
                            "sentAt": messages[0]["sentAt"],
                            "type": messages[0]["type"],
                        },
                    }
                    for fan, messages in chats[start:end]
                ],
                "nextCursor": str(end) if end < len(chats) else None,
            }
        )

    def _messages(self, creator: str, fan: str, query: Mapping[str, str]) -> FakeReply:
        """Newest first. `dateFilter` is null on the last page, as seen."""
        messages = self._chats.get(creator, {}).get(fan)
        if messages is None:
            return FakeReply.json_body(
                {"error": "Invalid user UUID"}, HTTPStatus.BAD_REQUEST
            )
        before = query.get("sentBefore")
        found = [m for m in messages if not before or m["sentAt"] < before]
        limit = int(query.get("limit", 50))
        page = found[:limit]
        more = len(found) > limit
        return FakeReply.json_body(
            {
                "data": page,
                "dateFilter": {"sentBefore": page[-1]["sentAt"]} if more else None,
            }
        )


_MANAGED = [
    {"uuid": CREATOR, "handle": CREATOR_HANDLE},
    {"uuid": OTHER_CREATOR, "handle": OTHER_HANDLE},
]


def _invalid_grant(description: str) -> FakeReply:
    return FakeReply.json_body(
        {"error": "invalid_grant", "error_description": description},
        HTTPStatus.BAD_REQUEST,
    )


@lru_cache(maxsize=1)
def fake_fanvue() -> FakeFanvue:
    return FakeFanvue()


def routes() -> dict[str, FakeHandler]:
    def api(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_fanvue().handle(request)

    def auth(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_fanvue().handle_auth(request)

    return {API_HOST: api, AUTH_HOST: auth}
