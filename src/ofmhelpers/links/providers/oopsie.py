"""Oopsie (oopsie.bio): each link is a page; ours are Direct Deeplinks
(`theme.page_type` "direct") with the 18+ gate (`theme.age_gate`) on.
API reference: links/CLAUDE.md."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ofmhelpers.links.errors import LinkError, SlugTakenError
from ofmhelpers.links.models import DayStat, RemoteLink, Usage
from ofmhelpers.links.providers.base import LinkProvider

if TYPE_CHECKING:
    from ofmhelpers.links.models import NewLink

_CREATED = 201
_NOT_FOUND = 404
_CONFLICT = 409


def _timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _destination(page: dict[str, Any]) -> str:
    """Where a page sends visitors: a deeplink's destination, else its first
    enabled link."""
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

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key}

    def _data(self, response: Any, what: str) -> Any:
        if not response.ok:
            raise self._failed(response, what)
        body = response.json()
        if not body.get("success"):
            raise self._failed(response, what)
        return body["data"]

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
        made = _remote(self._data(response, f"make {link.slug}"))
        if not made.adult:
            self.delete(made.remote_id)
            msg = f"Oopsie did not keep the 18+ gate on {link.slug}: link deleted"
            raise LinkError(msg)
        return made

    def list_links(self) -> list[RemoteLink]:
        pages = self._data(self._call("GET", "/api/pages"), "list pages")
        return [_remote(p) for p in pages]

    def _page(self, slug: str) -> dict[str, Any]:
        return self._data(
            self._call("GET", f"/api/pages/{quote(slug, safe='')}"), f"read {slug}"
        )

    def enable_adult(self, remote_id: str) -> None:
        # `theme` is replaced whole on PATCH: send it back with the gate on.
        theme = {**(self._page(remote_id).get("theme") or {}), "age_gate": True}
        response = self._call(
            "PATCH", f"/api/pages/{quote(remote_id, safe='')}", json={"theme": theme}
        )
        page = self._data(response, f"turn 18+ on for {remote_id}")
        if not (page.get("theme") or {}).get("age_gate"):
            msg = f"Oopsie did not keep the 18+ gate on {remote_id}"
            raise LinkError(msg)

    def delete(self, remote_id: str) -> None:
        response = self._call("DELETE", f"/api/pages/{quote(remote_id, safe='')}")
        if response.status_code != _NOT_FOUND:
            self._data(response, f"delete {remote_id}")

    def daily(
        self, remote_ids: list[str], start: date, end: date
    ) -> dict[str, list[DayStat]]:
        days = [start + timedelta(d) for d in range((end - start).days + 1)]
        # Labels are "MM-DD": unambiguous within the 93 days a range may span.
        by_label = {d.strftime("%m-%d"): d for d in days}
        found: dict[str, list[DayStat]] = {}
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
            )
            per = data.get("perSlug") or data["timeSeries"]
            series = per["series"]
            if isinstance(series, dict):  # one page: the plain shape
                series = [{"slug": slugs[0], **series}]
            for item in series:
                found[item["slug"]] = [
                    DayStat(day=by_label[label], views=v, clicks=c)
                    for label, v, c in zip(
                        per["labels"], item["views"], item["clicks"], strict=True
                    )
                    if label in by_label and (v or c)
                ]
        return found

    def usage(self) -> Usage:
        data = self._data(self._call("GET", "/api/account/usage"), "read usage")
        return Usage(
            used=data["pages_used"],
            limit=None if data.get("is_unlimited") else data.get("effective_limit"),
        )
