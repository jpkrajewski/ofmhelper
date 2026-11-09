"""Fanvue's public API, the read calls this app makes plus webhook destinations.
Through `utils.outbound`. Nothing here can message a fan or mark a chat read: reading
messages defaults to marking them read on Fanvue, so `markAsRead=false` goes on every
call."""

import time
from http import HTTPStatus
from typing import Any
lazy from collections.abc import Callable, Iterator

import requests
from pydantic import ValidationError
lazy from pydantic import BaseModel

from ofmhelpers.config import settings
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
from ofmhelpers.utils import outbound

logger = get_logger(__name__)


class FanvueClient:
    """One connected account. `token` gives a live access token each time it is
    asked, so a long round outlives the short-lived one it started with."""

    def __init__(self, token: Callable[[], str]) -> None:
        self._token = token
        self._session = outbound.mount(requests.Session())
        self._session.headers["X-Fanvue-API-Version"] = settings.fanvue.api_version

    def me(self) -> FanvueUser:
        return _parsed(FanvueUser, self._get("users/me"))

    def managed_creators(self) -> Iterator[ChatUser]:
        """The creators an agency account's seat is assigned to."""
        cursor: str | None = None
        while True:
            page = _parsed(
                CreatorsPage,
                self._get("creators", size=settings.fanvue.page_size, cursor=cursor),
            )
            yield from page.data
            if not page.next_cursor:
                return
            cursor = page.next_cursor

    def chats(self, creator_uuid: str | None) -> Iterator[FanvueChat]:
        """Chats, latest activity first; `creator_uuid` for an agency acting for a
        creator. Pages are fetched as they are consumed."""
        path = f"creators/{creator_uuid}/chats" if creator_uuid else "chats"
        cursor: str | None = None
        while True:
            page = _parsed(
                ChatsPage,
                self._get(path, size=settings.fanvue.page_size, cursor=cursor),
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
        """A chat's messages newest first, mass messages that reached this fan
        included, never marking them read."""
        prefix = f"creators/{creator_uuid}/chats" if creator_uuid else "chats"
        return _parsed(
            MessagesPage,
            self._get(
                f"{prefix}/{fan_uuid}/messages",
                limit=settings.fanvue.page_size,
                markAsRead="false",
                sentBefore=before.sent_before if before else None,
                receivedBefore=before.received_before if before else None,
            ),
        )

    def subscriptions(self) -> list[SubscriptionItem]:
        """The webhook destinations made for this account."""
        return _parsed(SubscriptionsPage, self._get("webhooks/subscriptions")).data

    def subscribe(self, url: str, events: list[str]) -> CreatedSubscriptionReply:
        """A new destination. Fanvue checks the address first with an unsigned call,
        and the signing secret comes only in this answer."""
        return _parsed(
            CreatedSubscriptionReply,
            self._change(
                "POST", "webhooks/subscriptions", json={"url": url, "events": events}
            ),
        )

    def unsubscribe(self, subscription_id: str) -> None:
        self._change("DELETE", f"webhooks/subscriptions/{subscription_id}")

    def _change(self, method: str, path: str, **kwargs: Any) -> Any:
        """Not retried: it may have worked."""
        try:
            r = self._session.request(
                method,
                f"{settings.fanvue.api_url}/{path}",
                headers=self._auth(),
                timeout=settings.fanvue.timeout_s,
                **kwargs,
            )
        except requests.RequestException as exc:
            msg = "Fanvue is not answering"
            raise FanvueError(msg) from exc
        return _ok(r)

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def _get(self, path: str, **params: Any) -> Any:
        """One read, tried again on a dropped connection, a 429 or a 5xx."""
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
            headers=self._auth(),
            timeout=settings.fanvue.timeout_s,
        )


def _parsed[T: BaseModel](model: type[T], reply: Any) -> T:
    """`reply` as `model`, or a `FanvueError`, so a caller has one failure to
    handle. The fields are named, never their values: they may be a fan's words."""
    try:
        return model.model_validate(reply)
    except ValidationError as exc:
        msg = f"Fanvue answered an unexpected {model.__name__}: {exc.error_count()} fields"
        raise FanvueError(msg) from None


def _chat(raw: dict[str, Any]) -> FanvueChat | None:
    """One chat of a page; None (logged) when it cannot be read, so it does not
    cost the rest of the page."""
    try:
        return FanvueChat.model_validate(raw)
    except ValidationError as exc:
        logger.warning("fanvue chat skipped: %d fields", exc.error_count())
        return None


def _retried(r: requests.Response) -> bool:
    return (
        r.status_code == HTTPStatus.TOO_MANY_REQUESTS
        or r.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
    )


def _retry_wait(r: requests.Response) -> float:
    """What a 429 or a database-timeout 503 asks for (capped), else the plain pause."""
    try:
        return min(float(r.headers["Retry-After"]), settings.fanvue.max_wait_s)
    except KeyError, ValueError:
        return settings.fanvue.retry_wait_s


def _ok(r: requests.Response) -> Any:
    """The answer's JSON, or the matching error."""
    if r.ok:
        try:
            return r.json() if r.content else None
        except ValueError as exc:
            msg = "Fanvue answered something that is not JSON"
            raise FanvueError(msg) from exc
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
