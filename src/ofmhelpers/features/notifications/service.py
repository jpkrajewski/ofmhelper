import asyncio
import time
from datetime import UTC, datetime
from functools import lru_cache
lazy from collections.abc import AsyncIterator, Callable

from pydantic import BaseModel
from redis.exceptions import RedisError

from ofmhelpers.auth.access import Area, opens
from ofmhelpers.cache.redis import get_redis, subscriber
from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats, drive_folders
from ofmhelpers.db.stores.jobs import list_jobs_page
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import ChatPlatform, DeliveryKind, StoredNotification
from ofmhelpers.schemas.notifications import Notification, Notifications
from ofmhelpers.schemas.persistence import JobStatus
lazy from ofmhelpers.features.trials.folder import GENERATE, REMAKE
lazy from ofmhelpers.features.trials.tasks import NOTHING_NEW, Made
lazy from ofmhelpers.schemas.chatting import OpenWait
lazy from ofmhelpers.schemas.drive import DriveTarget
lazy from ofmhelpers.schemas.persistence import Job
lazy from ofmhelpers.schemas.users import Viewer

logger = get_logger(__name__)

_CHANNEL = "notifications"
_CHATS = "chats"
_RESUBSCRIBE_S = 3.0
_CENTS = 100
_WORDS = {
    DeliveryKind.MASS: "Mass message sent",
    DeliveryKind.SUBSCRIBED: "New subscriber",
}
_THREADS, _TRIALS = "threads", "trials"
_JOBS_READ = 50


def _moment(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, UTC)


def money(cents: int | None, currency: str | None) -> str:
    amount = (cents or 0) / _CENTS
    text = f"{amount:.0f}" if amount.is_integer() else f"{amount:.2f}"
    return f"${text}" if currency in (None, "USD") else f"{currency} {text}"


def _event_text(row: StoredNotification) -> str:
    if row.kind == DeliveryKind.SALE:
        return f"Sold {money(row.amount_cents, row.currency)} {row.detail}".strip()
    return _WORDS[row.kind]


def _reconnects(now: float) -> list[Notification]:
    found = []
    for platform in ChatPlatform:
        for account in chats.accounts(platform):
            if account.needs_reconnect:
                item_id = f"reconnect:{platform.value}:{account.account_id}"
                found.append(
                    Notification(
                        id=item_id,
                        event_ids=[item_id],
                        kind="warn",
                        title=f"@{account.username}",
                        text=f"Connect {platform.label} again",
                        url=f"/chatting/{platform.value}",
                        at=_moment(now),
                    )
                )
    return found


def _waits(now: float) -> list[Notification]:
    late = settings.chatting.unanswered_after_s
    per_model: dict[tuple[ChatPlatform, str], list[OpenWait]] = {}
    for wait in sorted(chats.open_waits(before=now - late), key=lambda w: w.started_at):
        per_model.setdefault((wait.platform, wait.model_username), []).append(wait)
    return [
        Notification(
            id=f"waits:{platform.value}:{username}",
            event_ids=[wait.id for wait in waits],
            kind="warn",
            title=f"@{username}",
            text=f"Chats unanswered ({len(waits)})",
            url=platform.model_page(username),
            at=_moment(waits[-1].started_at + late),
        )
        for (platform, username), waits in per_model.items()
    ]


def _chat_events(now: float) -> list[Notification]:
    found = []
    for row in chats.notifications(since=now - settings.chatting.notification_shown_s):
        event_id = f"{row.platform.value}:{row.event_id}"
        found.append(
            Notification(
                id=event_id,
                event_ids=[event_id],
                title=f"@{row.model_username}",
                text=_event_text(row),
                url=row.platform.model_page(row.model_username),
                at=_moment(row.at),
            )
        )
    return found


class _ThreadsSet(BaseModel):
    login_id: int
    reel_name: str


class _TrialsRun(BaseModel):
    label: str
    login: int


def _made(made: Made) -> str:
    if not made.generated and not made.failed:
        return NOTHING_NEW
    text = f"{made.generated} made"
    return f"{text}, {made.failed} failed" if made.failed else text


def _job(job: Job, accounts: list[DriveTarget]) -> Notification | None:
    done = job.status is JobStatus.done
    if job.task == _THREADS:
        threads = _ThreadsSet.model_validate(job.params)
        login_id, page = threads.login_id, _THREADS
        text = f"Threads pictures {'ready' if done else 'failed'}: {threads.reel_name}"
    else:
        run = _TrialsRun.model_validate(job.params)
        if run.label not in (GENERATE, REMAKE):
            return None
        login_id, page = run.login, _TRIALS
        made = _made(Made.model_validate(job.result)) if done else ""
        text = f"Trials ready: {made}" if done else "Trials failed"
    item_id = f"job:{job.id}:{job.status.value}"
    handle = next((a.handle for a in accounts if a.login_id == login_id), None)
    return Notification(
        id=item_id,
        event_ids=[item_id],
        kind="info" if done else "warn",
        title=f"@{handle}" if handle else page.title(),
        text=text,
        url=f"/{page}/{login_id}",
        at=_moment(job.finished_at or job.created_at),
    )


def _jobs(user_id: int, now: float) -> list[Notification]:
    since = now - settings.chatting.notification_shown_s
    page = list_jobs_page({_THREADS, _TRIALS}, 0, _JOBS_READ, str(user_id))
    ended = [
        job
        for job in page.jobs
        if job.status is not JobStatus.running and (job.finished_at or 0) >= since
    ]
    if not ended:
        return []
    accounts = drive_folders.targets(user_id)
    return [n for job in ended if (n := _job(job, accounts)) is not None]


def read(viewer: Viewer) -> Notifications:
    now = time.time()
    found = _jobs(viewer.user.id, now)
    if opens(viewer, Area.CHATTING):
        found += _reconnects(now) + _waits(now) + _chat_events(now)
    return Notifications(items=sorted(found, key=lambda n: n.at, reverse=True))


def changed(user_id: int | None = None) -> None:
    try:
        get_redis().publish(_CHANNEL, _CHATS if user_id is None else str(user_id))
    except RedisError:
        logger.warning(
            "notifications change of %s not sent", user_id or _CHATS, exc_info=True
        )


class _Changes:
    def __init__(self) -> None:
        self.queues: set[asyncio.Queue[str]] = set()
        self.reader: asyncio.Task[None] | None = None

    async def _read(self) -> None:
        while True:
            try:
                async with subscriber() as redis, redis.pubsub() as pubsub:
                    await pubsub.subscribe(_CHANNEL)
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            for queue in self.queues:
                                queue.put_nowait(message["data"].decode())
            except Exception:
                logger.exception("notifications: subscription lost, subscribing again")
                await asyncio.sleep(_RESUBSCRIBE_S)

    def join(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self.queues.add(queue)
        loop = asyncio.get_running_loop()
        reader = self.reader
        if reader is None or reader.done() or reader.get_loop() is not loop:
            self.reader = loop.create_task(self._read())
        return queue

    def leave(self, queue: asyncio.Queue[str]) -> None:
        self.queues.discard(queue)
        if not self.queues and self.reader is not None:
            self.reader.cancel()
            self.reader = None


@lru_cache(maxsize=1)
def _changes() -> _Changes:
    return _Changes()


def _channels_of(viewer: Viewer) -> set[str]:
    return (
        {str(viewer.user.id), _CHATS}
        if opens(viewer, Area.CHATTING)
        else {str(viewer.user.id)}
    )


async def _next_change_of(viewer: Viewer, queue: asyncio.Queue[str]) -> None:
    channels = _channels_of(viewer)
    while True:
        seen = {await queue.get()}
        while not queue.empty():
            seen.add(queue.get_nowait())
        if seen & channels:
            return


async def updates(
    signed_in: Callable[[], Viewer | None],
) -> AsyncIterator[Notifications]:
    changes = _changes()
    queue = changes.join()
    try:
        while (viewer := await asyncio.to_thread(signed_in)) is not None:
            yield await asyncio.to_thread(read, viewer)
            await _next_change_of(viewer, queue)
    finally:
        changes.leave(queue)
