"""Oopsie and Bouncy faked at the wire (OFM_FAKE_EXTERNALS, tests): the
endpoints the clients in `providers/` call, answered the way each service
does (verified against the real ones 2026-10-22, links/CLAUDE.md), so the
real clients run unchanged. Nothing leaves the process.

Only `OOPSIE_FAKE_KEY` / `BOUNCY_FAKE_KEY` are accepted (401 otherwise).
State lives in memory, or, with `OFM_FAKES_DIR` set, in `oopsie.json` /
`bouncy.json` there under an exclusive file lock, so the app under e2e, its
worker processes and the test seeding them share one account. Test hooks:
`add_page`/`add_link`/`remove` (done on the dashboard), `set_traffic` (what the
analytics report), `set_limit` (the plan's quota); `pages`/`links`/`adult`
read the state."""

from __future__ import annotations

import fcntl
import json
import re
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import BaseModel, Field

from ofmhelpers.config import settings
from ofmhelpers.utils.outbound import FakeHandler, FakeReply

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    import requests

OOPSIE_FAKE_KEY = "gmp_live_fake"
BOUNCY_FAKE_KEY = "bcy_live_pk_fake"


def _body(request: requests.PreparedRequest) -> dict[str, Any]:
    raw = request.body if isinstance(request.body, (str, bytes)) else b"{}"
    return json.loads(raw or b"{}")


def _query(request: requests.PreparedRequest) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(request.url or "").query).items()}


def _path(request: requests.PreparedRequest) -> str:
    return unquote(urlsplit(request.url or "").path)


def _days(start: str, end: str) -> list[date]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [first + timedelta(d) for d in range((last - first).days + 1)]


class FakeAccount(BaseModel):
    """One provider account: its links by id, quota, forced 18+ gates
    (Bouncy), and traffic by "id|YYYY-MM-DD" -> [views, clicks]."""

    links: dict[str, dict[str, Any]] = Field(default_factory=dict)
    limit: int
    active_limit: int | None = None
    adult: list[str] = Field(default_factory=list)
    traffic: dict[str, list[int]] = Field(default_factory=dict)
    calls: list[str] = Field(default_factory=list)

    def numbers(self, link_id: str, day: date) -> tuple[int, int]:
        views, clicks = self.traffic.get(f"{link_id}|{day.isoformat()}", [0, 0])
        return views, clicks


class _Fake:
    """State handling both fakes share."""

    file_name: str
    default_limit: int
    default_active_limit: int | None = None

    def __init__(self, state_dir: str | None) -> None:
        self._path = Path(state_dir) / self.file_name if state_dir else None
        self._memory = self._fresh()
        self._lock = threading.Lock()

    def _fresh(self) -> FakeAccount:
        return FakeAccount(
            limit=self.default_limit, active_limit=self.default_active_limit
        )

    @contextmanager
    def _state(self) -> Iterator[FakeAccount]:
        """The state, locked (thread and, with a file, process); saved when
        the block ends."""
        with self._lock:
            if self._path is None:
                yield self._memory
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a+", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                fh.seek(0)
                raw = fh.read()
                state = FakeAccount.model_validate_json(raw) if raw else self._fresh()
                yield state
                fh.seek(0)
                fh.truncate()
                fh.write(state.model_dump_json())

    def set_traffic(self, link_id: str, day: date, views: int, clicks: int) -> None:
        with self._state() as state:
            state.traffic[f"{link_id}|{day.isoformat()}"] = [views, clicks]

    def remove(self, link_id: str) -> None:
        """A link deleted on the provider's own dashboard."""
        with self._state() as state:
            del state.links[link_id]

    def set_limit(self, limit: int) -> None:
        with self._state() as state:
            state.limit = limit

    @property
    def calls(self) -> list[str]:
        with self._state() as state:
            return list(state.calls)

    def _links(self) -> dict[str, dict[str, Any]]:
        with self._state() as state:
            return dict(state.links)


class FakeOopsie(_Fake):
    """A link is a page, by slug, as the API returns it."""

    file_name = "oopsie.json"
    default_limit = 50

    @property
    def pages(self) -> dict[str, dict[str, Any]]:
        return self._links()

    @staticmethod
    def _ok(data: Any, status: int = 200) -> FakeReply:
        return FakeReply.json_body({"success": True, "data": data}, status)

    @staticmethod
    def _no(error: str, status: int) -> FakeReply:
        return FakeReply.json_body({"success": False, "error": error}, status)

    @staticmethod
    def _page(slug: str, theme: dict[str, Any], name: str = "") -> dict[str, Any]:
        return {
            "id": f"page-{slug}",
            "slug": slug,
            "display_name": name or slug,
            "links": [],
            "is_active": True,
            "created_at": datetime.now(UTC).isoformat(),
            "theme": theme,
        }

    def add_page(self, slug: str, destination: str, *, age_gate: bool = True) -> None:
        theme = {
            "page_type": "direct",
            "destination_url": destination,
            "age_gate": age_gate,
            "preferred_public_host": "oopsie.bio",
        }
        with self._state() as state:
            state.links[slug] = self._page(slug, theme)

    def handle(self, request: requests.PreparedRequest) -> FakeReply:
        if request.headers.get("x-api-key") != OOPSIE_FAKE_KEY:
            return self._no("Unauthorized", 401)
        method, path = request.method or "GET", _path(request)
        with self._state() as state:
            state.calls.append(f"{method} {path}")
            if path == "/api/pages" and method == "GET":
                return self._ok(list(state.links.values()))
            if path == "/api/pages" and method == "POST":
                return self._create(state, _body(request))
            if path == "/api/account/usage":
                return self._ok(
                    {
                        "pages_used": len(state.links),
                        "effective_limit": state.limit,
                        "is_unlimited": False,
                    }
                )
            if path == "/api/analytics/activity":
                return self._activity(state, _body(request))
            if match := re.fullmatch(r"/api/pages/([^/]+)", path):
                return self._one(state, method, match[1], request)
        return self._no("Not found", 404)

    def _create(self, state: FakeAccount, body: dict[str, Any]) -> FakeReply:
        slug = body["slug"]
        if slug in state.links:
            return self._no("Slug already taken", 409)
        if len(state.links) >= state.limit:
            return self._no("Page limit reached for your plan", 402)
        page = self._page(slug, body["theme"], body.get("display_name", ""))
        state.links[slug] = page
        return self._ok(page, 201)

    def _one(
        self,
        state: FakeAccount,
        method: str,
        slug: str,
        request: requests.PreparedRequest,
    ) -> FakeReply:
        page = state.links.get(slug)
        if page is None:
            return self._no("Page not found", 404)
        if method == "DELETE":
            del state.links[slug]
            return self._ok({"deleted": True, "clicks_deleted": 0})
        if method == "PATCH":
            page.update(_body(request))
        return self._ok(page)

    def _activity(self, state: FakeAccount, body: dict[str, Any]) -> FakeReply:
        days = _days(body["from"], body["to"])
        series = [
            {
                "slug": slug,
                "views": [state.numbers(slug, d)[0] for d in days],
                "clicks": [state.numbers(slug, d)[1] for d in days],
            }
            for slug in body["scopeValue"].split(",")
            if slug in state.links
        ]
        labels = [d.strftime("%m-%d") for d in days]
        return self._ok(
            {"perSlug": {"labels": labels, "interval": "day", "series": series}}
        )


class FakeBouncy(_Fake):
    """Deeplinks by id, as the API returns them; `adult`: ids whose 18+
    gate is forced."""

    file_name = "bouncy.json"
    default_limit = 10
    default_active_limit = 5

    @property
    def links(self) -> dict[str, dict[str, Any]]:
        return self._links()

    @property
    def adult(self) -> set[str]:
        with self._state() as state:
            return set(state.adult)

    @staticmethod
    def _new(state: FakeAccount, slug: str, destination: str, domain: str) -> dict:
        active = sum(x["isActive"] for x in state.links.values()) < (
            state.active_limit or 0
        )
        link_id = slug if domain == "bouncy.ai" else f"{domain}__{slug}"
        link = {
            "id": link_id,
            "slug": slug,
            "domain": domain,
            "destination": destination,
            "url": f"https://{domain}/{slug}",
            "isActive": active,
            "type": "deeplink",
            "createdAt": {"_seconds": int(datetime.now(UTC).timestamp())},
        }
        state.links[link_id] = link
        return link

    def add_link(self, slug: str, destination: str, domain: str = "bouncy.ai") -> str:
        with self._state() as state:
            return self._new(state, slug, destination, domain)["id"]

    def handle(self, request: requests.PreparedRequest) -> FakeReply:
        if request.headers.get("Authorization") != f"Bearer {BOUNCY_FAKE_KEY}":
            return FakeReply.json_body(
                {"error": "Unauthorized", "message": "Invalid API key"}, 401
            )
        method, path = request.method or "GET", _path(request).removeprefix("/v1")
        with self._state() as state:
            state.calls.append(f"{method} {path}")
            if path == "/links" and method == "GET":
                return self._list(state, _query(request))
            if path == "/links" and method == "POST":
                return self._create(state, _body(request))
            if path == "/account/limits":
                return self._limits(state)
            if match := re.fullmatch(r"/analytics/links/(.+)", path):
                return self._analytics(state, match[1], _query(request))
            if match := re.fullmatch(r"/links/(.+)", path):
                return self._link(state, method, match[1], request)
        return FakeReply.json_body({"error": "Not Found"}, 404)

    @staticmethod
    def _limits(state: FakeAccount) -> FakeReply:
        links = {
            "total": len(state.links),
            "totalLimit": state.limit,
            "active": sum(x["isActive"] for x in state.links.values()),
            "activeLimit": state.active_limit,
        }
        return FakeReply.json_body({"success": True, "data": {"links": links}})

    @staticmethod
    def _list(state: FakeAccount, query: dict[str, str]) -> FakeReply:
        page, limit = int(query.get("page", 1)), int(query.get("limit", 50))
        items = list(state.links.values())
        return FakeReply.json_body(
            {
                "data": items[(page - 1) * limit : page * limit],
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "totalCount": len(items),
                    "hasMore": page * limit < len(items),
                },
            }
        )

    def _create(self, state: FakeAccount, body: dict[str, Any]) -> FakeReply:
        domain = body.get("domain", "bouncy.ai")
        slug = body["slug"].lower()
        if any(
            x["slug"] == slug and x["domain"] == domain for x in state.links.values()
        ):
            return FakeReply.json_body(
                {"error": "Custom slug or domain already taken"}, 400
            )
        if len(state.links) >= state.limit:
            return FakeReply.json_body(
                {
                    "error": "You have reached the maximum number of deeplinks",
                    "code": "TOTAL_LINK_LIMIT_REACHED",
                },
                403,
            )
        link = self._new(state, slug, body["url"], domain)
        return FakeReply.json_body({"success": True, "data": link}, 201)

    def _link(
        self,
        state: FakeAccount,
        method: str,
        link_id: str,
        request: requests.PreparedRequest,
    ) -> FakeReply:
        if link_id not in state.links:
            return FakeReply.json_body(
                {"error": "Not Found", "message": "Deeplink not found"}, 404
            )
        if method == "DELETE":
            del state.links[link_id]
            state.adult = [x for x in state.adult if x != link_id]
            return FakeReply.json_body({"success": True, "id": link_id})
        if method == "PUT":
            body = _body(request)
            known = {
                "destination",
                "title",
                "forceAdultWarning",
                "adultContentCheckpoint",
            }
            if body.get("forceAdultWarning") and link_id not in state.adult:
                state.adult.append(link_id)
            return FakeReply.json_body(
                {
                    "success": True,
                    "id": link_id,
                    "updatedFields": sorted(known & set(body)),
                }
            )
        return FakeReply.json_body({"success": True, "data": state.links[link_id]})

    @staticmethod
    def _analytics(
        state: FakeAccount, link_id: str, query: dict[str, str]
    ) -> FakeReply:
        if link_id not in state.links:
            return FakeReply.json_body({"error": "Not Found"}, 404)
        daily = [
            {"date": d.isoformat(), "views": views, "clicks": clicks}
            for d in _days(query["from"], query["to"])
            for views, clicks in [state.numbers(link_id, d)]
            if views or clicks
        ]
        return FakeReply.json_body({"id": link_id, "dailyViews": daily})


@lru_cache(maxsize=4)
def _fake_oopsie(state_dir: str) -> FakeOopsie:
    return FakeOopsie(state_dir or None)


@lru_cache(maxsize=4)
def _fake_bouncy(state_dir: str) -> FakeBouncy:
    return FakeBouncy(state_dir or None)


def fake_oopsie() -> FakeOopsie:
    return _fake_oopsie(settings.infra.fakes_dir)


def fake_bouncy() -> FakeBouncy:
    return _fake_bouncy(settings.infra.fakes_dir)


def routes() -> dict[str, FakeHandler]:
    def oopsie(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_oopsie().handle(request)

    def bouncy(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_bouncy().handle(request)

    return {"oopsie.bio": oopsie, "api.bouncy.ai": bouncy}
