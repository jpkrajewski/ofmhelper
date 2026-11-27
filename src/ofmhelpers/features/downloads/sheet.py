# Copyright (C) 2026 jpkrajewski

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.downloads import store
from ofmhelpers.db.downloads.schema import SheetQueued
from ofmhelpers.features.downloads.links import LinkDownloads
from ofmhelpers.features.refusals import (
    NotFoundError,
    RefusedError,
    TooSoonError,
    TryAgainError,
    UnavailableError,
)
from ofmhelpers.features.storage.service import StorageServiceSheet
lazy from ofmhelpers.features.storage.models import SheetColumn

logger = logging.getLogger(__name__)

BUSY = "Could not start the download. Try again."


def today() -> date:
    return datetime.now(ZoneInfo(settings.gdrive.timezone)).date()


class Clicker(BaseModel, frozen=True):
    user_id: int

    @property
    def _cooldown_key(self) -> str:
        return f"downloads:sheet-cooldown:{self.user_id}"

    def _claims_key(self, day: date) -> str:
        return f"downloads:sheet:{self.user_id}:{day.isoformat()}"

    def cooldown_left(self) -> int:
        try:
            left = int(get_redis().ttl(self._cooldown_key))
        except RedisError:
            logger.warning("sheet cooldown unreadable", exc_info=True)
            return 0
        return max(left, 0)

    def rest(self) -> None:
        try:
            redis = get_redis()
            seconds = settings.downloaders.sheet_cooldown_s
            if redis.set(self._cooldown_key, "1", nx=True, ex=seconds):
                return
            left = max(int(redis.ttl(self._cooldown_key)), 1)
        except RedisError:
            logger.warning("sheet cooldown unavailable, allowing", exc_info=True)
            return
        msg = f"Wait {left} s"
        raise TooSoonError(msg)

    def wake(self) -> None:
        try:
            get_redis().delete(self._cooldown_key)
        except RedisError:
            logger.warning("sheet cooldown not ended", exc_info=True)

    def claim(self, day: date, links: list[str]) -> list[str]:
        key = self._claims_key(day)
        fresh: list[str] = []
        try:
            redis = get_redis()
            for link in links:
                if redis.sadd(key, link):
                    fresh.append(link)  # noqa: PERF401
            redis.expire(key, settings.downloaders.sheet_claim_s)
        except RedisError as exc:
            logger.warning("sheet links not claimed", exc_info=True)
            self.unclaim(day, fresh)
            raise TryAgainError(BUSY) from exc
        return fresh

    def unclaim(self, day: date, links: list[str]) -> None:
        if not links:
            return
        try:
            get_redis().srem(self._claims_key(day), *links)
        except RedisError:
            logger.warning(
                "%d sheet link(s) of %s stay claimed for user %s",
                len(links),
                day,
                self.user_id,
                exc_info=True,
            )


class SheetDownloads(BaseModel, frozen=True):
    clicker: Clicker
    sheets: StorageServiceSheet
    links: LinkDownloads

    def cooldown_left(self) -> int:
        return self.clicker.cooldown_left()

    def _column(self, day: date) -> SheetColumn:
        name = settings.downloaders.sheet_name
        column = self.sheets.get_by_name(name).column(day)
        if column is None:
            msg = f"No column for {day:%d/%m/%Y} in {name}"
            raise NotFoundError(msg)
        return column

    def start(self) -> SheetQueued:
        clicker = self.clicker
        clicker.rest()
        day = today()
        try:
            column = self._column(day)
        except RefusedError, NotFoundError, UnavailableError:
            clicker.wake()
            raise
        mine = store.newest_of(self.links.owner_id, column.links)
        have = {file.url for file in mine}
        fresh = clicker.claim(day, [link for link in column.links if link not in have])
        answer = SheetQueued(
            day=day,
            new=len(fresh),
            already=len(column.links) - len(fresh),
            skipped=column.skipped,
            cooldown_s=clicker.cooldown_left(),
        )
        if not fresh:
            return answer
        try:
            job_id = self.links.queue(fresh, {"sheet_date": day.isoformat()})
        except Exception:
            clicker.unclaim(day, fresh)
            raise
        logger.info(
            "drive sheet: %d new link(s) of %s queued as %s",
            len(fresh),
            day,
            job_id,
        )
        return answer.model_copy(update={"job_id": job_id})
