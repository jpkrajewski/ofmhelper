"""Bouncy (api.bouncy.ai/v1): deeplinks on bouncy.ai or a system domain.
The 18+ gate is set by `PUT /links/{id}` with `forceAdultWarning` and
`adultContentCheckpoint` (undocumented on REST; the fields Bouncy's own MCP
`adult_warning` writes, verified 2026-10-22, links/CLAUDE.md). GET never
returns them, so a listed link's gate is unknown."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ofmhelpers.links.errors import LinkError, QuotaError, SlugTakenError
from ofmhelpers.links.models import DayStat, RemoteLink, Usage
from ofmhelpers.links.providers.base import LinkProvider
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    import requests

    from ofmhelpers.links.models import NewLink

logger = get_logger(__name__)

_FORBIDDEN = 403
_NOT_FOUND = 404
_PAGE_SIZE = 100
_ADULT = {"forceAdultWarning": True, "adultContentCheckpoint": True}
_QUOTA_CODES = {"TOTAL_LINK_LIMIT_REACHED", "plan_upgrade_required"}


def _code(response: requests.Response) -> str | None:
    """Bouncy's machine-readable error code, top level or nested."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return error.get("code")
    return body.get("code") if isinstance(body, dict) else None


def _timestamp(value: Any) -> float | None:
    """Bouncy sends Firestore `{"_seconds": ...}` or an ISO string."""
    if isinstance(value, dict) and "_seconds" in value:
        return float(value["_seconds"])
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    return None


def _spans(start: date, end: date, size: int) -> list[tuple[date, date]]:
    """`start`..`end` cut into spans of at most `size` days: a longer one
    comes back in monthly buckets (90 days did, 2026-10-22)."""
    spans = []
    while start <= end:
        last = min(start + timedelta(days=size - 1), end)
        spans.append((start, last))
        start = last + timedelta(days=1)
    return spans


def _remote(link: dict[str, Any], adult: bool | None = None) -> RemoteLink:
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

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

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
            logger.warning("bouncy %s: no 18+ gate, deleting it", made["id"])
            self.delete(made["id"])
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
            found += [_remote(x) for x in body["data"]]
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

    def daily(
        self, remote_ids: list[str], start: date, end: date
    ) -> dict[str, list[DayStat]]:
        found: dict[str, list[DayStat]] = {}
        for remote_id in remote_ids:
            per_day: dict[date, list[int]] = {}
            for first, last in _spans(start, end, self.info().max_span_days):
                for bucket in self._buckets(remote_id, first, last):
                    day = date.fromisoformat(bucket["date"][:10])
                    totals = per_day.setdefault(day, [0, 0])
                    totals[0] += bucket.get("views", 0)
                    totals[1] += bucket.get("clicks", 0)
            found[remote_id] = [
                DayStat(day=day, views=views, clicks=clicks)
                for day, (views, clicks) in sorted(per_day.items())
            ]
        return found

    def _buckets(self, remote_id: str, first: date, last: date) -> list[dict]:
        """`dailyViews` of one span: days, or hours ("YYYY-MM-DD HH") for a
        one-day span."""
        response = self._call(
            "GET",
            f"/analytics/links/{quote(remote_id, safe='')}",
            params={"from": first.isoformat(), "to": last.isoformat()},
        )
        if not response.ok:
            raise self._failed(response, f"read analytics of {remote_id}")
        return response.json().get("dailyViews", [])

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
