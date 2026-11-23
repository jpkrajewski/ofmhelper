# Copyright (C) 2026 jpkrajewski

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel

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
    SlugTakenError,
    UngatedError,
)
from ofmhelpers.integrations.short_links.providers.base import LinkProvider, listed
from ofmhelpers.integrations.short_links.providers.oopsie_wire import (
    ActivityPoint,
    LinkActivity,
    OopsieActivity,
    OopsieHeaders,
    OopsiePageData,
    OopsiePages,
    OopsieSeries,
    OopsieUsage,
)
lazy from ofmhelpers.db.short_links.catalog.schema import NewLink

_CREATED = 201
_HOUR_LABEL = re.compile(r"\d{2}:00")
_NOT_FOUND = 404
_CONFLICT = 409


def _timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:  # silent: not a date
        return None


def _destination(page: dict[str, Any]) -> str:
    theme = page.get("theme") or {}
    if theme.get("page_type") == "direct":
        return theme.get("destination_url") or ""
    enabled = [x for x in page.get("links") or [] if x.get("enabled", True)]
    return enabled[0].get("url", "") if enabled else ""


def _remote(page: dict[str, Any]) -> RemoteLink:
    theme = page.get("theme") or {}
    domain = theme.get("preferred_public_host") or "oopsie.bio"
    return RemoteLink(
        remote_id=page["slug"],
        slug=page["slug"],
        domain=domain,
        url=f"https://{domain}/{page['slug']}",
        destination=_destination(page),
        active=bool(page.get("is_active")),
        adult=bool(theme.get("age_gate")),
        created_at=_timestamp(page.get("created_at")),
    )


class OopsieProvider(LinkProvider):
    key = "oopsie"

    def _headers(self) -> OopsieHeaders:
        return OopsieHeaders(api_key=self.api_key)

    def _checked(self, response: Any, what: str) -> None:
        if not response.ok:
            raise self._failed(response, what)
        if not response.json().get("success"):
            raise self._failed(response, what)

    def _data[M: BaseModel](self, response: Any, what: str, shape: type[M]) -> M:
        self._checked(response, what)
        return shape.model_validate(response.json()["data"])

    def create(self, link: NewLink) -> RemoteLink:
        response = self._call(
            "POST",
            "/api/pages",
            json={
                "slug": link.slug,
                "display_name": link.title or link.slug,
                "links": [],
                "is_active": True,
                "theme": {
                    "preferred_public_host": link.domain,
                    "page_type": "direct",
                    "destination_url": link.destination,
                    "age_gate": True,
                    "deeplink_escape": True,
                    "traffic_source": "instagram",
                },
            },
        )
        if response.status_code == _CONFLICT:
            msg = f"Oopsie: {link.slug} is taken"
            raise SlugTakenError(msg)
        if response.status_code != _CREATED:
            raise self._failed(response, f"make {link.slug}")
        made = _remote(
            self._data(response, f"make {link.slug}", OopsiePageData).model_dump(),
        )
        if not made.adult:
            try:
                self.delete(made.remote_id)
            except LinkError as exc:
                msg = (
                    f"Oopsie: {link.slug} is live without the 18+ gate and could"
                    " not be deleted: Turn on 18+ or delete it"
                )
                raise UngatedError(msg, made) from exc
            msg = f"Oopsie did not keep the 18+ gate on {link.slug}: link deleted"
            raise LinkError(msg)
        return made

    def list_links(self) -> list[RemoteLink]:
        pages = self._data(self._call("GET", "/api/pages"), "list pages", OopsiePages)
        return listed(self.key, pages.root, _remote)

    def _page(self, slug: str) -> OopsiePageData:
        return self._data(
            self._call("GET", f"/api/pages/{quote(slug, safe='')}"),
            f"read {slug}",
            OopsiePageData,
        )

    def enable_adult(self, remote_id: str) -> None:
        theme = {**(self._page(remote_id).theme or {}), "age_gate": True}
        response = self._call(
            "PATCH",
            f"/api/pages/{quote(remote_id, safe='')}",
            json={"theme": theme},
        )
        page = self._data(response, f"turn 18+ on for {remote_id}", OopsiePageData)
        if not (page.theme or {}).get("age_gate"):
            msg = f"Oopsie did not keep the 18+ gate on {remote_id}"
            raise LinkError(msg)

    def delete(self, remote_id: str) -> None:
        response = self._call("DELETE", f"/api/pages/{quote(remote_id, safe='')}")
        if response.status_code != _NOT_FOUND:
            self._checked(response, f"delete {remote_id}")

    def daily(self, remote_ids: list[str], start: date, end: date) -> list[LinkDays]:
        days = [start + timedelta(d) for d in range((end - start).days + 1)]
        by_label = {d.strftime("%m-%d"): d for d in days}
        return [
            LinkDays(
                remote_id=found.slug,
                days=[
                    DayStat(day=by_label[p.label], views=p.views, clicks=p.clicks)
                    for p in found.points
                    if p.label in by_label and (p.views or p.clicks)
                ],
            )
            for found in self._activity(remote_ids, start, end)
        ]

    def hourly(self, remote_ids: list[str], day: date) -> list[LinkHours]:
        return [
            LinkHours(
                remote_id=found.slug,
                hours=[
                    HourStat(
                        hour=datetime.combine(day, time(int(p.label[:2])), tzinfo=UTC),
                        views=p.views,
                        clicks=p.clicks,
                    )
                    for p in found.points
                    if _HOUR_LABEL.fullmatch(p.label) and (p.views or p.clicks)
                ],
            )
            for found in self._activity(remote_ids, day, day)
        ]

    def _activity(
        self,
        remote_ids: list[str],
        start: date,
        end: date,
    ) -> list[LinkActivity]:
        found: dict[str, list[ActivityPoint]] = {}
        batch = self.info().stats_batch
        for i in range(0, len(remote_ids), batch):
            slugs = remote_ids[i : i + batch]
            data = self._data(
                self._call(
                    "POST",
                    "/api/analytics/activity",
                    json={
                        "range": "custom",
                        "from": start.isoformat(),
                        "to": end.isoformat(),
                        "tz": "UTC",
                        "scope": "pages",
                        "scopeValue": ",".join(slugs),
                        "breakdown": "slug",
                    },
                ),
                "read analytics",
                OopsieActivity,
            )
            per = data.per_slug or data.time_series
            if per is None:
                msg = "Oopsie sent no analytics numbers"
                raise LinkError(msg)
            series = per.series
            if isinstance(series, OopsieSeries):
                series = [
                    series.model_copy(update={"slug": slugs[0]})
                    if series.slug is None
                    else series,
                ]
            for item in series:
                if item.slug is None:
                    msg = "Oopsie sent analytics without a slug"
                    raise LinkError(msg)
                found[item.slug] = [
                    ActivityPoint(label=label, views=views, clicks=clicks)
                    for label, views, clicks in zip(
                        per.labels,
                        item.views,
                        item.clicks,
                        strict=True,
                    )
                ]
        return [LinkActivity(slug=s, points=p) for s, p in found.items()]

    def usage(self) -> Usage:
        data = self._data(
            self._call("GET", "/api/account/usage"),
            "read usage",
            OopsieUsage,
        )
        return Usage(
            used=data.pages_used,
            limit=None if data.is_unlimited else data.effective_limit,
        )
