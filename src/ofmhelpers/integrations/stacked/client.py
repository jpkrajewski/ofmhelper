# Copyright (C) 2026 jpkrajewski

import hashlib
import logging
import time
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any
lazy from collections.abc import Iterable, Iterator

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.stacked.errors import (
    StackedAuthError,
    StackedError,
    StackedForbiddenError,
)
from ofmhelpers.integrations.stacked.models import (
    AccessibleCreator,
    AccountType,
    ChatsPage,
    ChatSummary,
    CreatedWebhook,
    CreatorsPage,
    Earnings,
    EarningsPage,
    ErrorReply,
    Me,
    Message,
    MessagesPage,
    SubscriberSummary,
    SubscriberSummaryPage,
    WebhookDestination,
    WebhooksPage,
)

logger = logging.getLogger(__name__)


class KeyInfo(BaseModel):
    agency: bool
    creators: list[AccessibleCreator]


class StackedClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._session = outbound.mount(requests.Session())
        self._session.headers["Authorization"] = f"Bearer {api_key}"

    @property
    def key_hint(self) -> str:
        return f"...{self._api_key[-4:]}"

    def same_key(self, other: StackedClient) -> bool:
        return self._api_key == other._api_key

    def me(self) -> Me:
        return _parsed(Me, self._get("me"))

    def info(self) -> KeyInfo:
        kept_at = (
            "stacked:key:" + hashlib.sha256(self._api_key.encode()).hexdigest()[:24]
        )
        raw = get_text(kept_at)
        if raw is not None:
            try:
                return KeyInfo.model_validate_json(raw)
            except ValidationError:
                logger.warning(
                    "stacked key %s: kept info unreadable",
                    self.key_hint,
                    exc_info=True,
                )
        info = KeyInfo(
            agency=self.me().account.type is AccountType.AGENCY,
            creators=self.creators(),
        )
        keep = settings.stacked
        ttl = keep.agency_refresh_s if info.agency else keep.creator_key_keep_s
        set_text(kept_at, info.model_dump_json(), ttl)
        return info

    def creators(self) -> list[AccessibleCreator]:
        return _parsed(CreatorsPage, self._get("creators")).creators

    def chats(self, creator_user_id: str) -> Iterator[ChatSummary]:
        cursor: str | None = None
        while True:
            page = _parsed(
                ChatsPage,
                self._get(
                    "chats",
                    {
                        "creatorUserId": creator_user_id,
                        "limit": settings.stacked.page_size,
                        "cursor": cursor,
                    },
                ),
            )
            for raw in page.chats:
                chat = _chat(raw)
                if chat is not None:
                    yield chat
            if not page.next_cursor:
                return
            cursor = page.next_cursor

    def messages(
        self,
        creator_user_id: str,
        channel_url: str,
        limit: int | None = None,
        before_message_id: int | None = None,
    ) -> list[Message]:
        return _parsed(
            MessagesPage,
            self._get(
                f"chats/{channel_url}/messages",
                {
                    "creatorUserId": creator_user_id,
                    "limit": limit or settings.stacked.page_size,
                    "beforeMessageId": before_message_id,
                },
            ),
        ).messages

    def earnings(
        self,
        creator_user_id: str,
        start: datetime,
        end: datetime,
    ) -> Earnings | None:
        page = _parsed(
            EarningsPage,
            self._get(
                "earnings",
                _window(creator_user_id, start, end).model_dump(by_alias=True),
            ),
        )
        return page.earnings[0] if page.earnings else None

    def subscriber_summary(
        self,
        creator_user_id: str,
        start: datetime,
        end: datetime,
    ) -> SubscriberSummary | None:
        page = _parsed(
            SubscriberSummaryPage,
            self._get(
                "subscribers/summary",
                _window(creator_user_id, start, end).model_dump(by_alias=True),
            ),
        )
        return page.summaries[0] if page.summaries else None

    def webhooks(self, creator_user_id: str) -> list[WebhookDestination]:
        reply = self._get("webhooks", {"creatorUserId": creator_user_id})
        return _parsed(WebhooksPage, reply).webhooks

    def create_webhook(
        self,
        creator_user_id: str,
        url: str,
        events: list[str],
    ) -> CreatedWebhook:
        return _parsed(
            CreatedWebhook,
            self._change(
                "POST",
                "webhooks",
                creator_user_id,
                json={"url": url, "events": events},
            ),
        )

    def delete_webhook(self, creator_user_id: str, webhook_id: str) -> None:
        self._change("DELETE", f"webhooks/{webhook_id}", creator_user_id)

    def _change(
        self,
        method: str,
        path: str,
        creator_user_id: str,
        json: dict[str, Any] | None = None,
    ) -> requests.Response:
        try:
            r = self._session.request(
                method,
                f"{settings.stacked.api_url}/{path}",
                params={"creatorUserId": creator_user_id},
                timeout=settings.stacked.timeout_s,
                json=json,
            )
        except requests.RequestException as exc:
            msg = "Stacked is not answering"
            raise StackedError(msg) from exc
        return _ok(r)

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        params = params or {}
        for attempt in range(1, settings.stacked.tries):
            try:
                r = self._request(path, params)
            except requests.RequestException:
                logger.warning(
                    "stacked GET %s try %d failed",
                    path,
                    attempt,
                    exc_info=True,
                )
                time.sleep(settings.stacked.retry_wait_s)
                continue
            if not _retried(r):
                return _ok(r)
            logger.warning(
                "stacked GET %s try %d answered %s",
                path,
                attempt,
                r.status_code,
            )
            time.sleep(_retry_wait(r))
        try:
            return _ok(self._request(path, params))
        except requests.RequestException as e:
            logger.warning("stacked GET %s gave up", path, exc_info=True)
            msg = "Stacked is not answering"
            raise StackedError(msg) from e

    def _request(self, path: str, params: dict[str, Any]) -> requests.Response:
        return self._session.get(
            f"{settings.stacked.api_url}/{path}",
            params=params,
            timeout=settings.stacked.timeout_s,
        )


class Reached(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    client: StackedClient
    creator: AccessibleCreator


class Reach(BaseModel):
    found: list[Reached]
    failed: bool
    refused: bool = False


class _KeyReach(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    client: StackedClient
    info: KeyInfo


def reach(api_keys: Iterable[str]) -> Reach:
    read: list[_KeyReach] = []
    failed = False
    refused = False
    for api_key in api_keys:
        client = StackedClient(api_key)
        try:
            read.append(_KeyReach(client=client, info=client.info()))
        except StackedAuthError, StackedForbiddenError:
            logger.warning("stacked key %s refused", client.key_hint, exc_info=True)
            refused = True
        except StackedError:
            logger.warning("stacked key %s unusable", client.key_hint, exc_info=True)
            failed = True
    found: dict[str, Reached] = {}
    for key in sorted(read, key=lambda k: not k.info.agency):
        for creator in key.info.creators:
            found.setdefault(
                creator.user_id,
                Reached(client=key.client, creator=creator),
            )
    return Reach(found=list(found.values()), failed=failed, refused=refused)


def reachable(api_keys: Iterable[str]) -> list[Reached]:
    return reach(api_keys).found


def _rfc3339(at: datetime) -> str:
    return at.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class _Window(BaseModel):
    creator_user_id: str = Field(serialization_alias="creatorUserId")
    start_time: str = Field(serialization_alias="startTime")
    end_time: str = Field(serialization_alias="endTime")


def _window(creator_user_id: str, start: datetime, end: datetime) -> _Window:
    return _Window(
        creator_user_id=creator_user_id,
        start_time=_rfc3339(start),
        end_time=_rfc3339(end),
    )


def _parsed[T: BaseModel](model: type[T], reply: requests.Response) -> T:
    try:
        body = reply.json() if reply.content else None
    except ValueError as exc:
        msg = "Stacked answered something that is not JSON"
        raise StackedError(msg) from exc
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        msg = (
            f"Stacked answered an unexpected "
            f"{model.__name__}: {exc.error_count()} fields"
        )
        raise StackedError(msg) from None


def _chat(raw: dict[str, Any]) -> ChatSummary | None:
    try:
        return ChatSummary.model_validate(raw)
    except ValidationError as exc:  # silent: the error quotes a chat, a fan's words
        logger.warning(
            "stacked chat %s skipped: %d fields",
            raw.get("channelUrl"),
            exc.error_count(),
        )
        return None


def _retried(r: requests.Response) -> bool:
    return (
        r.status_code == HTTPStatus.TOO_MANY_REQUESTS
        or r.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
    )


def _retry_wait(r: requests.Response) -> float:
    try:
        return min(float(r.headers["Retry-After"]), settings.stacked.max_wait_s)
    except KeyError, ValueError:  # silent: no Retry-After number: the plain pause
        return settings.stacked.retry_wait_s


def _ok(r: requests.Response) -> requests.Response:
    if r.ok:
        return r
    try:
        detail = ErrorReply.model_validate_json(r.content).error
    except ValidationError:  # silent: not Stacked's error body: the reason
        detail = r.reason
    logger.warning(
        "stacked %s %s: %s %s (request id %s)",
        r.request.method,
        (r.url or "").split("?")[0],
        r.status_code,
        detail,
        r.headers.get("X-Request-Id"),
    )
    if r.status_code == HTTPStatus.UNAUTHORIZED:
        msg = f"Wrong Stacked API key: {detail}"
        raise StackedAuthError(msg)
    if r.status_code == HTTPStatus.FORBIDDEN:
        msg = f"Stacked refused this: {detail}"
        raise StackedForbiddenError(msg)
    msg = f"Stacked error {r.status_code}: {detail}"
    raise StackedError(msg)
