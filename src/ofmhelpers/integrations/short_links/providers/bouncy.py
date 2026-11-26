# Copyright (C) 2026 jpkrajewski

from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote
lazy from collections.abc import Mapping

from pydantic import BaseModel, Field
lazy import requests

from ofmhelpers.db.short_links.catalog.schema import (
    DayStat,
    HourStat,
    LinkDays,
    LinkHours,
    RemoteLink,
    Usage,
)
from ofmhelpers.integrations.short_links.errors import (
    LinkError,
    QuotaError,
    SlugTakenError,
    UngatedError,
)
from ofmhelpers.integrations.short_links.providers.base import (
    LinkProvider,
    RequestHeaders,
    listed,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.short_links.catalog.schema import NewLink

logger = get_logger(__name__)

_FORBIDDEN = 403
_NOT_FOUND = 404
_PAGE_SIZE = 100
_ADULT = {"forceAdultWarning": True, "adultContentCheckpoint": True}
_QUOTA_CODES = {"TOTAL_LINK_LIMIT_REACHED", "plan_upgrade_required"}


def _code(response: requests.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:  # silent: not JSON: no code
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return error.get("code")
    return body.get("code") if isinstance(body, dict) else None


def _timestamp(value: Mapping[str, Any] | str | None) -> float | None:
    if isinstance(value, dict) and "_seconds" in value:
        return float(value["_seconds"])
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:  # silent: not a date
            return None
    return None


def _hour_of(label: str) -> datetime:
    if len(label) > len("YYYY-MM-DD"):
        return datetime.strptime(label[:13], "%Y-%m-%d %H").replace(tzinfo=UTC)
    return datetime.fromisoformat(label[:10]).replace(tzinfo=UTC)


class BouncyHeaders(RequestHeaders):
    authorization: str = Field(serialization_alias="Authorization")


class Span(BaseModel, frozen=True):
    first: date
    last: date


class Bucket(BaseModel):
    date: str
    views: int = 0
    clicks: int = 0


def _spans(start: date, end: date, size: int) -> list[Span]:
    spans: list[Span] = []
    while start <= end:
        last = min(start + timedelta(days=size - 1), end)
        spans.append(Span(first=start, last=last))
        start = last + timedelta(days=1)
    return spans


def _remote(link: dict[str, Any], adult: bool | None = None) -> RemoteLink:  # noqa: FBT001
    return RemoteLink(
        remote_id=link["id"],
        slug=link.get("slug") or link["id"],
        domain=link.get("domain") or "bouncy.ai",
        url=link["url"],
        destination=link.get("destination") or "",
        active=bool(link.get("isActive")),
        adult=adult,
        created_at=_timestamp(link.get("createdAt")),
    )


class BouncyProvider(LinkProvider):
    key = "bouncy"

    def _headers(self) -> BouncyHeaders:
        return BouncyHeaders(authorization=f"Bearer {self.api_key}")

    @staticmethod
    def _path(remote_id: str) -> str:
        return f"/links/{quote(remote_id, safe='')}"

    def create(self, link: NewLink) -> RemoteLink:
        body: dict[str, Any] = {
            "url": link.destination,
            "slug": link.slug,
            "domain": link.domain,
        }
        if self.info().behavior:
            body["behavior"] = self.info().behavior
        response = self._call("POST", "/links", json=body)
        if not response.ok:
            message = self._error(response)
            if "taken" in message.lower():
                msg = f"Bouncy: {link.domain}/{link.slug} is taken"
                raise SlugTakenError(msg)
            if response.status_code == _FORBIDDEN and _code(response) in _QUOTA_CODES:
                msg = f"Bouncy plan limit reached: {message}"
                raise QuotaError(msg)
            raise self._failed(response, f"make {link.domain}/{link.slug}")
        made = response.json()["data"]
        try:
            self.enable_adult(made["id"])
        except LinkError:
            logger.warning(
                "bouncy %s: no 18+ gate, deleting it",
                made["id"],
                exc_info=True,
            )
            try:
                self.delete(made["id"])
            except LinkError as exc:
                msg = (
                    f"Bouncy: {made['id']} is live without the 18+ gate and could"
                    " not be deleted: Turn on 18+ or delete it"
                )
                raise UngatedError(msg, _remote(made, adult=False)) from exc
            raise
        return _remote(made, adult=True)

    def list_links(self) -> list[RemoteLink]:
        found: list[RemoteLink] = []
        page = 1
        while True:
            response = self._call(
                "GET",
                "/links",
                params={"page": page, "limit": _PAGE_SIZE, "type": "deeplink"},
            )
            if not response.ok:
                raise self._failed(response, "list links")
            body = response.json()
            found += listed(self.key, body["data"], _remote)
            if not body["pagination"]["hasMore"]:
                return found
            page += 1

    def enable_adult(self, remote_id: str) -> None:
        response = self._call("PUT", self._path(remote_id), json=_ADULT)
        if not response.ok:
            raise self._failed(response, f"turn 18+ on for {remote_id}")
        if "forceAdultWarning" not in response.json().get("updatedFields", []):
            msg = f"Bouncy did not take the 18+ gate on {remote_id}"
            raise LinkError(msg)

    def delete(self, remote_id: str) -> None:
        response = self._call("DELETE", self._path(remote_id))
        if not response.ok and response.status_code != _NOT_FOUND:
            raise self._failed(response, f"delete {remote_id}")

    def daily(self, remote_ids: list[str], start: date, end: date) -> list[LinkDays]:
        found: list[LinkDays] = []
        for remote_id in dict.fromkeys(remote_ids):
            days = self._days(remote_id, start, end)
            if days is not None:
                found.append(LinkDays(remote_id=remote_id, days=days))
        return found

    def hourly(self, remote_ids: list[str], day: date) -> list[LinkHours]:
        found: list[LinkHours] = []
        for remote_id in dict.fromkeys(remote_ids):
            buckets = self._buckets(remote_id, day, day)
            if buckets is not None:
                hours = [
                    HourStat(
                        hour=_hour_of(bucket.date),
                        views=bucket.views,
                        clicks=bucket.clicks,
                    )
                    for bucket in buckets
                    if bucket.views or bucket.clicks
                ]
                found.append(LinkHours(remote_id=remote_id, hours=hours))
        return found

    def _days(self, remote_id: str, start: date, end: date) -> list[DayStat] | None:
        per_day: dict[date, list[int]] = {}
        for span in _spans(start, end, self.info().max_span_days):
            buckets = self._buckets(remote_id, span.first, span.last)
            if buckets is None:
                return None
            for bucket in buckets:
                day = date.fromisoformat(bucket.date[:10])
                totals = per_day.setdefault(day, [0, 0])
                totals[0] += bucket.views
                totals[1] += bucket.clicks
        return [
            DayStat(day=day, views=views, clicks=clicks)
            for day, (views, clicks) in sorted(per_day.items())
        ]

    def _buckets(self, remote_id: str, first: date, last: date) -> list[Bucket] | None:
        response = self._call(
            "GET",
            f"/analytics/links/{quote(remote_id, safe='')}",
            params={"from": first.isoformat(), "to": last.isoformat()},
        )
        if response.status_code == _NOT_FOUND:
            logger.warning("bouncy link %s is gone: its numbers are skipped", remote_id)
            return None
        if not response.ok:
            raise self._failed(response, f"read analytics of {remote_id}")
        return [Bucket.model_validate(b) for b in response.json().get("dailyViews", [])]

    def usage(self) -> Usage:
        response = self._call("GET", "/account/limits")
        if not response.ok:
            raise self._failed(response, "read limits")
        links = response.json()["data"]["links"]

        def limit(value: int) -> int | None:
            return None if value < 0 else value

        return Usage(
            used=links["total"],
            limit=limit(links["totalLimit"]),
            active=links["active"],
            active_limit=limit(links["activeLimit"]),
        )
