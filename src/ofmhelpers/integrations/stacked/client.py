"""Stacked's public API, the read calls this app makes. Through
`integrations.outbound`, so OFM_FAKE_EXTERNALS answers it from `stacked/fake.py`.

One client works for both kinds of key: a creator key reaches its one creator,
an agency key every linked creator, and `creators()` lists them either way.
The only writes are webhook destinations: nothing can message a fan or mark a
chat read."""

import time
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any
lazy from collections.abc import Iterable, Iterator

import requests
from pydantic import BaseModel, ConfigDict, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.stacked.errors import (
    StackedAuthError,
    StackedError,
    StackedForbiddenError,
)
from ofmhelpers.integrations.stacked.models import (
    AccessibleCreator,
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
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class StackedClient:
    """One API key, of a creator or of an agency."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._session = outbound.mount(requests.Session())
        self._session.headers["Authorization"] = f"Bearer {api_key}"

    @property
    def key_hint(self) -> str:
        """The key's tail, for logs (never the key)."""
        return f"...{self._api_key[-4:]}"

    def me(self) -> Me:
        """The account this key acts as."""
        return _parsed(Me, self._get("me"))

    def creators(self) -> list[AccessibleCreator]:
        """Every creator this key can act on."""
        return _parsed(CreatorsPage, self._get("creators")).creators

    def chats(self, creator_user_id: str) -> Iterator[ChatSummary]:
        """`creator_user_id`'s chats, latest last message first. Pages are
        fetched as they are consumed: stop iterating to stop calling."""
        cursor: str | None = None
        while True:
            page = _parsed(
                ChatsPage,
                self._get(
                    "chats",
                    creatorUserId=creator_user_id,
                    limit=settings.stacked.page_size,
                    cursor=cursor,
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
        """A chat's messages, newest first; older ones from
        `before_message_id` (exclusive)."""
        return _parsed(
            MessagesPage,
            self._get(
                f"chats/{channel_url}/messages",
                creatorUserId=creator_user_id,
                limit=limit or settings.stacked.page_size,
                beforeMessageId=before_message_id,
            ),
        ).messages

    def earnings(
        self, creator_user_id: str, start: datetime, end: datetime
    ) -> Earnings | None:
        """Realized sales in [start, end) (any window, to the second; refunds
        left out). Scope `insights:read`. None when Stacked has no row."""
        page = _parsed(
            EarningsPage, self._get("earnings", **_window(creator_user_id, start, end))
        )
        return page.earnings[0] if page.earnings else None

    def subscriber_summary(
        self, creator_user_id: str, start: datetime, end: datetime
    ) -> SubscriberSummary | None:
        """Subscribers gained and lost in [start, end). Scope `insights:read`."""
        page = _parsed(
            SubscriberSummaryPage,
            self._get("subscribers/summary", **_window(creator_user_id, start, end)),
        )
        return page.summaries[0] if page.summaries else None

    def webhooks(self, creator_user_id: str) -> list[WebhookDestination]:
        """The destinations this key made for the creator."""
        reply = self._get("webhooks", creatorUserId=creator_user_id)
        return _parsed(WebhooksPage, reply).webhooks

    def create_webhook(
        self, creator_user_id: str, url: str, events: list[str]
    ) -> CreatedWebhook:
        """A new destination. Its signing secret comes only in this answer."""
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
        self, method: str, path: str, creator_user_id: str, **kwargs: Any
    ) -> Any:
        """Not retried: it may have worked."""
        try:
            r = self._session.request(
                method,
                f"{settings.stacked.api_url}/{path}",
                params={"creatorUserId": creator_user_id},
                timeout=settings.stacked.timeout_s,
                **kwargs,
            )
        except requests.RequestException as exc:
            msg = "Stacked is not answering"
            raise StackedError(msg) from exc
        return _ok(r)

    def _get(self, path: str, **params: Any) -> Any:
        """One read, tried again on a dropped connection, a 429 or a 5xx."""
        for attempt in range(1, settings.stacked.tries):
            try:
                r = self._request(path, params)
            except requests.RequestException:
                logger.warning(
                    "stacked GET %s try %d failed", path, attempt, exc_info=True
                )
                time.sleep(settings.stacked.retry_wait_s)
                continue
            if not _retried(r):
                return _ok(r)
            logger.warning(
                "stacked GET %s try %d answered %s", path, attempt, r.status_code
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
    """A creator and the client to use for it."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    client: StackedClient
    creator: AccessibleCreator


class Reach(BaseModel):
    """Every creator the keys reach, with the client to use for each. `failed`:
    Stacked did not answer for a key, so someone may be missing. A key it refuses
    is not that: it stays refused, and trying again changes nothing."""

    found: list[Reached]
    failed: bool


def reach(api_keys: Iterable[str]) -> Reach:
    """A creator key gives its creator, an agency key each linked one. A creator
    two keys reach stays with the first. A key that cannot be read is skipped
    (logged), so one revoked key does not take the others down."""
    found: dict[str, Reached] = {}
    failed = False
    for api_key in api_keys:
        client = StackedClient(api_key)
        try:
            creators = client.creators()
        except StackedAuthError, StackedForbiddenError:
            logger.warning("stacked key %s refused", client.key_hint, exc_info=True)
            continue
        except StackedError:
            logger.warning("stacked key %s unusable", client.key_hint, exc_info=True)
            failed = True
            continue
        for creator in creators:
            found.setdefault(creator.user_id, Reached(client=client, creator=creator))
    return Reach(found=list(found.values()), failed=failed)


def reachable(api_keys: Iterable[str]) -> list[Reached]:
    """Just the creators, for a caller that does not care who may be missing."""
    return reach(api_keys).found


def _rfc3339(at: datetime) -> str:
    return at.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _window(creator_user_id: str, start: datetime, end: datetime) -> dict[str, str]:
    """An insights call's creator and window: `startTime` inclusive, `endTime`
    exclusive, RFC3339 UTC."""
    return {
        "creatorUserId": creator_user_id,
        "startTime": _rfc3339(start),
        "endTime": _rfc3339(end),
    }


def _parsed[T: BaseModel](model: type[T], reply: Any) -> T:
    """`reply` as `model`, or a `StackedError`, so a caller has one failure to
    handle. The fields are named, never their values: they may be a fan's words."""
    try:
        return model.model_validate(reply)
    except ValidationError as exc:
        msg = f"Stacked answered an unexpected {model.__name__}: {exc.error_count()} fields"
        raise StackedError(msg) from None


def _chat(raw: dict[str, Any]) -> ChatSummary | None:
    """One chat of a page; None (logged) when it cannot be read, so it does not
    cost the rest of the page."""
    try:
        return ChatSummary.model_validate(raw)
    except ValidationError as exc:
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
    """What a 429 asks for (capped), else the plain pause."""
    try:
        return min(float(r.headers["Retry-After"]), settings.stacked.max_wait_s)
    except KeyError, ValueError:
        return settings.stacked.retry_wait_s


def _ok(r: requests.Response) -> Any:
    """The answer's JSON, or the matching `StackedError`. Branches on the
    status: the API says its messages may change."""
    if r.ok:
        try:
            return r.json() if r.content else None
        except ValueError as exc:
            msg = "Stacked answered something that is not JSON"
            raise StackedError(msg) from exc
    try:
        detail = ErrorReply.model_validate_json(r.content).error
    except ValidationError:
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
