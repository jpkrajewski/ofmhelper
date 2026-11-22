import time
from http import HTTPStatus
from typing import Any
lazy from collections.abc import Callable, Iterator

import requests
from pydantic import BaseModel, Field, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.fanvue.errors import (
    FanvueAuthError,
    FanvueError,
    FanvueRefusedError,
)
from ofmhelpers.integrations.fanvue.models import (
    ChatsPage,
    ChatUser,
    CreatedSubscriptionReply,
    CreatorsPage,
    DateFilter,
    FanvueChat,
    FanvueUser,
    MessagesPage,
    SubscriptionItem,
    SubscriptionsPage,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class BearerHeader(BaseModel):
    authorization: str = Field(serialization_alias="Authorization")


class FanvueClient:
    def __init__(self, token: Callable[[], str]) -> None:
        self._token = token
        self._session = outbound.mount(requests.Session())
        self._session.headers["X-Fanvue-API-Version"] = settings.fanvue.api_version

    def me(self) -> FanvueUser:
        return _parsed(FanvueUser, self._get("users/me"))

    def managed_creators(self) -> Iterator[ChatUser]:
        cursor: str | None = None
        while True:
            page = _parsed(
                CreatorsPage,
                self._get(
                    "creators", {"size": settings.fanvue.page_size, "cursor": cursor}
                ),
            )
            yield from page.data
            if not page.next_cursor:
                return
            cursor = page.next_cursor

    def chats(self, creator_uuid: str | None) -> Iterator[FanvueChat]:
        path = f"creators/{creator_uuid}/chats" if creator_uuid else "chats"
        cursor: str | None = None
        while True:
            page = _parsed(
                ChatsPage,
                self._get(path, {"size": settings.fanvue.page_size, "cursor": cursor}),
            )
            for raw in page.data:
                chat = _chat(raw)
                if chat is not None:
                    yield chat
            if not page.next_cursor:
                return
            cursor = page.next_cursor

    def messages(
        self,
        creator_uuid: str | None,
        fan_uuid: str,
        before: DateFilter | None = None,
    ) -> MessagesPage:
        prefix = f"creators/{creator_uuid}/chats" if creator_uuid else "chats"
        return _parsed(
            MessagesPage,
            self._get(
                f"{prefix}/{fan_uuid}/messages",
                {
                    "limit": settings.fanvue.page_size,
                    "markAsRead": "false",
                    "sentBefore": before.sent_before if before else None,
                    "receivedBefore": before.received_before if before else None,
                },
            ),
        )

    def subscriptions(self) -> list[SubscriptionItem]:
        return _parsed(SubscriptionsPage, self._get("webhooks/subscriptions")).data

    def subscribe(self, url: str, events: list[str]) -> CreatedSubscriptionReply:
        return _parsed(
            CreatedSubscriptionReply,
            self._change(
                "POST", "webhooks/subscriptions", json={"url": url, "events": events}
            ),
        )

    def unsubscribe(self, subscription_id: str) -> None:
        self._change("DELETE", f"webhooks/subscriptions/{subscription_id}")

    def _change(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> requests.Response:
        try:
            r = self._session.request(
                method,
                f"{settings.fanvue.api_url}/{path}",
                headers=self._auth().model_dump(by_alias=True),
                timeout=settings.fanvue.timeout_s,
                json=json,
            )
        except requests.RequestException as exc:
            msg = "Fanvue is not answering"
            raise FanvueError(msg) from exc
        return _ok(r)

    def _auth(self) -> BearerHeader:
        return BearerHeader(authorization=f"Bearer {self._token()}")

    def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> requests.Response:
        params = params or {}
        for attempt in range(1, settings.fanvue.tries):
            try:
                r = self._request(path, params)
            except requests.RequestException:
                logger.warning(
                    "fanvue GET %s try %d failed", path, attempt, exc_info=True
                )
                time.sleep(settings.fanvue.retry_wait_s)
                continue
            if not _retried(r):
                return _ok(r)
            logger.warning(
                "fanvue GET %s try %d answered %s", path, attempt, r.status_code
            )
            time.sleep(_retry_wait(r))
        try:
            return _ok(self._request(path, params))
        except requests.RequestException as e:
            logger.warning("fanvue GET %s gave up", path, exc_info=True)
            msg = "Fanvue is not answering"
            raise FanvueError(msg) from e

    def _request(self, path: str, params: dict[str, Any]) -> requests.Response:
        return self._session.get(
            f"{settings.fanvue.api_url}/{path}",
            params=params,
            headers=self._auth().model_dump(by_alias=True),
            timeout=settings.fanvue.timeout_s,
        )


def _parsed[T: BaseModel](model: type[T], reply: requests.Response) -> T:
    try:
        body = reply.json() if reply.content else None
    except ValueError as exc:
        msg = "Fanvue answered something that is not JSON"
        raise FanvueError(msg) from exc
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        msg = f"Fanvue answered an unexpected {model.__name__}: {exc.error_count()} fields"
        raise FanvueError(msg) from None


def _chat(raw: dict[str, Any]) -> FanvueChat | None:
    try:
        return FanvueChat.model_validate(raw)
    except ValidationError as exc:  # silent: the error quotes a chat, a fan's words
        logger.warning("fanvue chat skipped: %d fields", exc.error_count())
        return None


def _retried(r: requests.Response) -> bool:
    return (
        r.status_code == HTTPStatus.TOO_MANY_REQUESTS
        or r.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
    )


def _retry_wait(r: requests.Response) -> float:
    try:
        return min(float(r.headers["Retry-After"]), settings.fanvue.max_wait_s)
    except KeyError, ValueError:  # silent: no Retry-After number: the plain pause
        return settings.fanvue.retry_wait_s


def _ok(r: requests.Response) -> requests.Response:
    if r.ok:
        return r
    logger.warning(
        "fanvue %s %s: %s",
        r.request.method,
        (r.url or "").split("?")[0],
        r.status_code,
    )
    if r.status_code == HTTPStatus.UNAUTHORIZED:
        msg = "Fanvue refused the connection"
        raise FanvueAuthError(msg)
    msg = f"Fanvue error {r.status_code}"
    if r.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
    ):
        raise FanvueRefusedError(msg)
    raise FanvueError(msg)
