"""Driving a chat platform's fake and signing its deliveries, the same way for each,
so a test says what happens and runs against every platform. A new platform = one class
here and one entry in `PLATFORMS`; the tests in test_chat_platforms.py then cover it."""

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from ofmhelpers.db.stores import chats
from ofmhelpers.features.chatting.sources import source_for
from ofmhelpers.features.chatting.sync import sync
from ofmhelpers.features.chatting.webhooks import cover_models
from ofmhelpers.integrations.fanvue import fake as fanvue_fake
from ofmhelpers.integrations.outbound import FakeReply
from ofmhelpers.integrations.stacked import fake as stacked_fake
from ofmhelpers.schemas.chatting import (
    ChatAccount,
    ChatPlatform,
    ChatTokens,
    ChatWebhook,
)
lazy from ofmhelpers.integrations.chat_source import ChatSource

STACKED_SECRET = (
    "whsec_" + base64.b64encode(b"stacked-test-secret-not-real-0001").decode()
)
FANVUE_SECRET = "whsec_" + "ab" * 32


class StackedDriver:
    platform = ChatPlatform.STACKED
    secret = STACKED_SECRET
    model_id = stacked_fake.CREATOR
    model = "fake_creator"

    def __init__(self, monkeypatch) -> None:
        self._monkeypatch = monkeypatch
        self.fake = stacked_fake.fake_stacked()
        self._next_id = 100

    @property
    def source(self) -> ChatSource:
        found = source_for(self.platform)
        assert found is not None
        return found

    def connect(self) -> None:
        self._monkeypatch.setenv("STACKED_API_KEY_TEST", stacked_fake.CREATOR_KEY)
        self._monkeypatch.setenv("OFM_STACKED_RETRY_WAIT_S", "0")
        chats.save_webhook(
            self.platform,
            ChatWebhook(
                model_id=self.model_id,
                model_username=self.model,
                webhook_id="wh_test",
                url="https://example.invalid/events/stacked",
                secret=self.secret,
            ),
        )

    def channel(self, fan: str) -> str:
        return f"channel_{fan}"

    def new_id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    # --- what happens on the platform, for the sync to find ------------------

    def _chat_of(self, fan: str) -> str:
        channel = self.channel(fan)
        known = self.fake.chats.get(self.model_id, [])
        if not any(c["channelUrl"] == channel for c in known):
            self.fake.add_chat(self.model_id, fan, channel)
        return channel

    def fan_writes(self, fan: str, at: float) -> str:
        message = self.fake.add_message(self._chat_of(fan), fan, "hi", int(at * 1000))
        return str(message.message_id)

    def model_replies(self, fan: str, at: float) -> str:
        channel = self._chat_of(fan)
        message = self.fake.add_message(channel, self.model_id, "hey", int(at * 1000))
        return str(message.message_id)

    def fail_next_reads(self, count: int, status: int = HTTPStatus.BAD_GATEWAY) -> None:
        self.fake.fail_next.extend(
            FakeReply.json_body({"error": "down"}, status) for _ in range(count)
        )

    def reads(self) -> list[tuple[str, str]]:
        return list(self.fake.calls)

    def chat_reads(self) -> int:
        """How many times this model's chats were read."""
        return len([p for _, p in self.fake.calls if "/chats" in p])

    # --- what the platform delivers ------------------------------------------

    def _message_event(
        self, kind: str, fan: str, message_id: str, *, from_fan: bool, at: float
    ) -> dict[str, Any]:
        user = fan if from_fan else self.model_id
        return {
            "id": f"{kind}:{message_id}",
            "type": kind,
            "createdAt": "2026-11-05T00:00:00Z",
            "data": {
                "creatorUserId": self.model_id,
                "channel": {"channelUrl": self.channel(fan)},
                "message": {
                    "messageId": int(message_id),
                    "userId": user,
                    "createdAt": int(at * 1000),
                    "type": "",
                    "customType": "",
                    "text": "test words",
                },
                "sender": {"userId": user, "nickname": fan},
            },
        }

    def message(self, fan: str, message_id: str, *, from_fan: bool, at: float) -> dict:
        return self._message_event(
            "message.created", fan, message_id, from_fan=from_fan, at=at
        )

    def deleted(self, fan: str, message_id: str, at: float) -> dict:
        return self._message_event(
            "message.deleted", fan, message_id, from_fan=True, at=at
        )

    def mass(self, mass_id: str) -> dict:
        return {
            "id": f"mass_message.completed:{mass_id}",
            "type": "mass_message.completed",
            "createdAt": "2026-11-05T00:00:00Z",
            "data": {
                "creatorUserId": self.model_id,
                "massMessageId": mass_id,
                "state": "completed",
            },
        }

    def mass_for_someone_else(self, mass_id: str) -> dict:
        body = self.mass(mass_id)
        body["data"]["creatorUserId"] = "nobody"
        return body

    def message_without_its_model(self, fan: str, message_id: str, at: float) -> dict:
        body = self.message(fan, message_id, from_fan=True, at=at)
        del body["data"]["creatorUserId"]
        return body

    def message_from_name(self, name: str, message_id: str, at: float) -> dict:
        body = self.message("ann", message_id, from_fan=True, at=at)
        body["data"]["sender"]["nickname"] = name
        return body

    def event_nobody_uses(self) -> dict:
        return {
            "id": "x",
            "type": "post.published",
            "createdAt": "2026-11-05T00:00:00Z",
            "data": {"creatorUserId": self.model_id},
        }

    def signed(
        self, body: dict, secret: str | None = None, age_s: float = 0
    ) -> tuple[bytes, dict[str, str]]:
        raw = json.dumps(body).encode()
        delivery = f"msg_{hashlib.sha256(raw).hexdigest()[:16]}"
        stamp = str(int(time.time() - age_s))
        key = base64.b64decode((secret or self.secret).removeprefix("whsec_"))
        mac = hmac.new(key, f"{delivery}.{stamp}.".encode() + raw, hashlib.sha256)
        return raw, {
            "svix-id": delivery,
            "svix-timestamp": stamp,
            "svix-signature": f"v1,{base64.b64encode(mac.digest()).decode()}",
            "content-type": "application/json",
        }

    # --- a model's emoji ------------------------------------------------------

    def react(
        self, fan: str, message_id: str, at: float, by_model: bool = True
    ) -> None:
        """Stacked says it only on the chat's messages, so the sync has to look."""
        who = self.model_id if by_model else fan
        self.fake.add_reaction(self.channel(fan), int(message_id), who, int(at * 1000))

    def notice_reaction(self, client, fan: str, message_id: str, at: float) -> None:  # noqa: ARG002
        sync(self.source)


class FanvueDriver:
    platform = ChatPlatform.FANVUE
    secret = FANVUE_SECRET
    model_id = fanvue_fake.CREATOR
    model = fanvue_fake.CREATOR_HANDLE

    def __init__(self, monkeypatch) -> None:
        self._monkeypatch = monkeypatch
        self.fake = fanvue_fake.fake_fanvue()
        self._next_id = 100
        self._reactions: dict[str, bool] = {}

    @property
    def source(self) -> ChatSource:
        found = source_for(self.platform)
        assert found is not None
        return found

    def connect(self) -> None:
        self._monkeypatch.setenv("FANVUE_CLIENT_ID", "fake-client")
        self._monkeypatch.setenv("FANVUE_CLIENT_SECRET", "fake-secret")
        self._monkeypatch.setenv("FANVUE_EVENT_SECRET", self.secret)
        self._monkeypatch.setenv("OFM_FANVUE_RETRY_WAIT_S", "0")
        tokens = self.fake.new_tokens()
        chats.save_account(
            self.platform,
            ChatAccount(
                account_id=fanvue_fake.AGENCY,
                username=fanvue_fake.AGENCY_HANDLE,
                is_agency=True,
            ),
            ChatTokens(
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                expires_at=time.time() + tokens.expires_in,
            ),
        )
        cover_models(self.source, self.source.models().found)

    def channel(self, fan: str) -> str:
        return f"{self.model_id}:{fan}"

    def new_id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    # --- what happens on the platform, for the sync to find ------------------

    def fan_writes(self, fan: str, at: float) -> str:
        return self.fake.add_message(self.model_id, fan, from_fan=True, at=at)

    def model_replies(self, fan: str, at: float) -> str:
        return self.fake.add_message(self.model_id, fan, from_fan=False, at=at)

    def fail_next_reads(self, count: int, status: int = HTTPStatus.BAD_GATEWAY) -> None:
        self.fake.fail_next.extend(
            FakeReply.json_body({"error": "down"}, status) for _ in range(count)
        )

    def reads(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path, _ in self.fake.calls]

    def chat_reads(self) -> int:
        """How many times this model's chats were read."""
        mine = f"/creators/{self.model_id}/chats"
        return len([p for _, p, _ in self.fake.calls if mine in p])

    # --- what the platform delivers ------------------------------------------

    def _party(self, fan: str) -> dict[str, Any]:
        return {"uuid": fan, "email": None, "display_name": fan, "handle": fan}

    def _message_event(
        self, kind: str, fan: str, message_id: str, *, from_fan: bool, at: float
    ) -> dict[str, Any]:
        return {
            "id": f"{kind}:{message_id}",
            "type": kind,
            "timestamp": "2026-11-05T00:00:00Z",
            "data": {
                "object": "message",
                "uuid": message_id,
                "sender": "fan" if from_fan else "creator",
                "created_at": _iso(at),
                "text": "test words",
                "creator": {"uuid": self.model_id},
                "fan": self._party(fan),
            },
        }

    def message(self, fan: str, message_id: str, *, from_fan: bool, at: float) -> dict:
        kind = "creator.message.received" if from_fan else "creator.message.sent"
        return self._message_event(kind, fan, message_id, from_fan=from_fan, at=at)

    def deleted(self, fan: str, message_id: str, at: float) -> dict:
        return self._message_event(
            "creator.message.deleted", fan, message_id, from_fan=True, at=at
        )

    def mass(self, mass_id: str) -> dict:
        return {
            "id": f"evt_{mass_id}",
            "type": "creator.message.mass_sent",
            "timestamp": "2026-11-05T00:00:00Z",
            "data": {
                "object": "mass_message",
                "uuid": mass_id,
                "sender": "creator",
                "recipient_count": 3,
                "creator": {"uuid": self.model_id},
            },
        }

    def mass_for_someone_else(self, mass_id: str) -> dict:
        body = self.mass(mass_id)
        body["data"]["creator"] = {"uuid": "nobody"}
        return body

    def message_without_its_model(self, fan: str, message_id: str, at: float) -> dict:
        body = self.message(fan, message_id, from_fan=True, at=at)
        del body["data"]["creator"]
        return body

    def message_from_name(self, name: str, message_id: str, at: float) -> dict:
        body = self.message("ann", message_id, from_fan=True, at=at)
        body["data"]["fan"]["display_name"] = name
        return body

    def event_nobody_uses(self) -> dict:
        return {
            "id": "x",
            "type": "creator.post.liked",
            "timestamp": "2026-11-05T00:00:00Z",
            "data": {"object": "post", "creator": {"uuid": self.model_id}},
        }

    def signed(
        self, body: dict, secret: str | None = None, age_s: float = 0
    ) -> tuple[bytes, dict[str, str]]:
        raw = json.dumps(body).encode()
        stamp = str(int(time.time() - age_s))
        key = (secret or self.secret).encode()
        mac = hmac.new(key, f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
        return raw, {
            "x-fanvue-signature": f"t={stamp},v0={mac}",
            "content-type": "application/json",
        }

    # --- a model's emoji ------------------------------------------------------

    def react(
        self, fan: str, message_id: str, at: float, by_model: bool = True
    ) -> None:
        """Fanvue says it only by webhook."""
        del fan, at
        self._reactions[message_id] = by_model

    def notice_reaction(self, client, fan: str, message_id: str, at: float) -> None:
        by_model = self._reactions[message_id]
        party = self._party(fan)
        raw, headers = self.signed(
            {
                "id": f"reaction:{message_id}",
                "type": "creator.message.reaction",
                "timestamp": "2026-11-05T00:00:00Z",
                "data": {
                    "object": "reaction",
                    "sender": "creator" if by_model else "fan",
                    "message_uuid": message_id,
                    "emoji": "fire",
                    "actor": {"uuid": self.model_id} if by_model else party,
                    "creator": {"uuid": self.model_id},
                    "fan": party,
                    "created_at": _iso(at),
                    "metadata": {},
                },
            }
        )
        reply = client.post("/events/fanvue", content=raw, headers=headers)
        assert reply.status_code == HTTPStatus.OK


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


PLATFORMS = {"stacked": StackedDriver, "fanvue": FanvueDriver}
