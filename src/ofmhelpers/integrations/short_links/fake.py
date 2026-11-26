# Copyright (C) 2026 jpkrajewski

import fcntl
import json
import re
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
lazy from collections.abc import Iterator, Mapping

from pydantic import BaseModel, Field
lazy import requests

from ofmhelpers.config import settings
from ofmhelpers.integrations.outbound import FakeReply, FakeRoute

OOPSIE_FAKE_KEY = "gmp_live_fake"
BOUNCY_FAKE_KEY = "bcy_live_pk_fake"


def _body[M: BaseModel](request: requests.PreparedRequest, shape: type[M]) -> M:
    raw = request.body if isinstance(request.body, (str, bytes)) else b"{}"
    return shape.model_validate(json.loads(raw or b"{}"))


def _query[M: BaseModel](request: requests.PreparedRequest, shape: type[M]) -> M:
    found = parse_qs(urlsplit(request.url or "").query)
    return shape.model_validate({k: v[0] for k, v in found.items()})


def _path(request: requests.PreparedRequest) -> str:
    return unquote(urlsplit(request.url or "").path)


def _days(start: str, end: str) -> list[date]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [first + timedelta(d) for d in range((last - first).days + 1)]


class Traffic(BaseModel, frozen=True):
    views: int = 0
    clicks: int = 0


class OopsieCreate(BaseModel, extra="allow"):
    slug: str
    theme: dict[str, Any]
    display_name: str = ""


class OopsiePatch(BaseModel, extra="allow"):
    pass


class OopsieActivityRequest(BaseModel, extra="allow"):
    first: str = Field(alias="from")
    last: str = Field(alias="to")
    scope_value: str = Field(alias="scopeValue")


class BouncyCreate(BaseModel, extra="allow"):
    url: str
    slug: str
    domain: str = "bouncy.ai"


class BouncyUpdate(BaseModel, extra="allow"):
    pass


class BouncyPage(BaseModel):
    page: int = 1
    limit: int = 50


class BouncyRange(BaseModel):
    first: str = Field(alias="from")
    last: str = Field(alias="to")


class OopsiePage(BaseModel, extra="allow"):
    id: str
    slug: str
    display_name: str
    links: list[Any]
    is_active: bool
    created_at: str
    theme: dict[str, Any]


class BouncyLink(BaseModel):
    id: str
    slug: str
    domain: str
    destination: str
    url: str
    is_active: bool = Field(alias="isActive")
    type: str
    created_at: dict[str, int] = Field(alias="createdAt")


class FakeAccount(BaseModel):
    links: dict[str, dict[str, Any]] = Field(default_factory=dict)
    limit: int
    active_limit: int | None = None
    adult: list[str] = Field(default_factory=list)
    traffic: dict[str, list[int]] = Field(default_factory=dict)
    hours: dict[str, list[int]] = Field(default_factory=dict)
    calls: list[str] = Field(default_factory=list)

    def numbers(self, link_id: str, day: date) -> Traffic:
        views, clicks = self.traffic.get(f"{link_id}|{day.isoformat()}", [0, 0])
        return Traffic(views=views, clicks=clicks)

    def hour_numbers(self, link_id: str, day: date, hour: int) -> Traffic:
        set_hours = [k for k in self.hours if k.startswith(f"{link_id}|{day}T")]
        if set_hours:
            views, clicks = self.hours.get(f"{link_id}|{day}T{hour:02}", [0, 0])
            return Traffic(views=views, clicks=clicks)
        return self.numbers(link_id, day) if hour == 0 else Traffic()


class _Fake:
    file_name: str
    default_limit: int
    default_active_limit: int | None = None

    def __init__(self, state_dir: str | None) -> None:
        self._path = Path(state_dir) / self.file_name if state_dir else None
        self._memory = self._fresh()
        self._lock = threading.Lock()

    def _fresh(self) -> FakeAccount:
        return FakeAccount(
            limit=self.default_limit,
            active_limit=self.default_active_limit,
        )

    @contextmanager
    def _state(self) -> Iterator[FakeAccount]:
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

    def set_hour_traffic(
        self,
        link_id: str,
        hour: datetime,
        views: int,
        clicks: int,
    ) -> None:
        at = hour.astimezone(UTC)
        with self._state() as state:
            state.hours[f"{link_id}|{at.date()}T{at.hour:02}"] = [views, clicks]
            day = f"{link_id}|{at.date()}"
            state.traffic[day] = [
                sum(v[0] for k, v in state.hours.items() if k.startswith(f"{day}T")),
                sum(v[1] for k, v in state.hours.items() if k.startswith(f"{day}T")),
            ]

    def remove(self, link_id: str) -> None:
        with self._state() as state:
            del state.links[link_id]

    def set_limit(self, limit: int) -> None:
        with self._state() as state:
            state.limit = limit

    @property
    def calls(self) -> list[str]:
        with self._state() as state:
            return list(state.calls)


class FakeOopsie(_Fake):
    file_name = "oopsie.json"
    default_limit = 50

    @property
    def pages(self) -> MappingProxyType[str, OopsiePage]:
        with self._state() as state:
            return MappingProxyType(
                {
                    slug: OopsiePage.model_validate(page)
                    for slug, page in state.links.items()
                },
            )

    @staticmethod
    def _ok(
        data: Mapping[str, Any] | list[Any],
        status: int = 200,
    ) -> FakeReply:
        return FakeReply.json_body({"success": True, "data": data}, status)

    @staticmethod
    def _no(error: str, status: int) -> FakeReply:
        return FakeReply.json_body({"success": False, "error": error}, status)

    @staticmethod
    def _page(slug: str, theme: dict[str, Any], name: str = "") -> OopsiePage:
        return OopsiePage(
            id=f"page-{slug}",
            slug=slug,
            display_name=name or slug,
            links=[],
            is_active=True,
            created_at=datetime.now(UTC).isoformat(),
            theme=theme,
        )

    def add_page(self, slug: str, destination: str, *, age_gate: bool = True) -> None:
        theme = {
            "page_type": "direct",
            "destination_url": destination,
            "age_gate": age_gate,
            "preferred_public_host": "oopsie.bio",
        }
        with self._state() as state:
            state.links[slug] = self._page(slug, theme).model_dump()

    def handle(self, request: requests.PreparedRequest) -> FakeReply:  # noqa: PLR0911
        if request.headers.get("x-api-key") != OOPSIE_FAKE_KEY:
            return self._no("Unauthorized", 401)
        method, path = request.method or "GET", _path(request)
        with self._state() as state:
            state.calls.append(f"{method} {path}")
            if path == "/api/pages" and method == "GET":
                return self._ok(list(state.links.values()))
            if path == "/api/pages" and method == "POST":
                return self._create(state, _body(request, OopsieCreate))
            if path == "/api/account/usage":
                return self._ok(
                    {
                        "pages_used": len(state.links),
                        "effective_limit": state.limit,
                        "is_unlimited": False,
                    },
                )
            if path == "/api/analytics/activity":
                return self._activity(state, _body(request, OopsieActivityRequest))
            if match := re.fullmatch(r"/api/pages/([^/]+)", path):
                return self._one(state, method, match[1], request)
        return self._no("Not found", 404)

    def _create(self, state: FakeAccount, body: OopsieCreate) -> FakeReply:
        slug = body.slug
        if slug in state.links:
            return self._no("Slug already taken", 409)
        if len(state.links) >= state.limit:
            return self._no("Page limit reached for your plan", 402)
        page = self._page(slug, body.theme, body.display_name).model_dump()
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
            page.update(_body(request, OopsiePatch).model_dump())
        return self._ok(page)

    def _activity(self, state: FakeAccount, body: OopsieActivityRequest) -> FakeReply:
        days = _days(body.first, body.last)
        slugs = [s for s in body.scope_value.split(",") if s in state.links]
        if len(days) == 1:
            [day] = days
            series = [
                {
                    "slug": slug,
                    "views": [
                        state.hour_numbers(slug, day, h).views for h in range(24)
                    ],
                    "clicks": [
                        state.hour_numbers(slug, day, h).clicks for h in range(24)
                    ],
                }
                for slug in slugs
            ]
            labels = [f"{h:02}:00" for h in range(24)]
            interval = "hour"
        else:
            series = [
                {
                    "slug": slug,
                    "views": [state.numbers(slug, d).views for d in days],
                    "clicks": [state.numbers(slug, d).clicks for d in days],
                }
                for slug in slugs
            ]
            labels = [d.strftime("%m-%d") for d in days]
            interval = "day"
        return self._ok(
            {"perSlug": {"labels": labels, "interval": interval, "series": series}},
        )


class FakeBouncy(_Fake):
    file_name = "bouncy.json"
    default_limit = 10
    default_active_limit = 5

    @property
    def links(self) -> MappingProxyType[str, BouncyLink]:
        with self._state() as state:
            return MappingProxyType(
                {
                    link_id: BouncyLink.model_validate(link)
                    for link_id, link in state.links.items()
                },
            )

    @property
    def adult(self) -> set[str]:
        with self._state() as state:
            return set(state.adult)

    @staticmethod
    def _new(
        state: FakeAccount,
        slug: str,
        destination: str,
        domain: str,
    ) -> BouncyLink:
        active = sum(x["isActive"] for x in state.links.values()) < (
            state.active_limit or 0
        )
        link_id = slug if domain == "bouncy.ai" else f"{domain}__{slug}"
        link = BouncyLink(
            id=link_id,
            slug=slug,
            domain=domain,
            destination=destination,
            url=f"https://{domain}/{slug}",
            isActive=active,
            type="deeplink",
            createdAt={"_seconds": int(datetime.now(UTC).timestamp())},
        )
        state.links[link_id] = link.model_dump(by_alias=True)
        return link

    def add_link(self, slug: str, destination: str, domain: str = "bouncy.ai") -> str:
        with self._state() as state:
            return self._new(state, slug, destination, domain).id

    def handle(self, request: requests.PreparedRequest) -> FakeReply:  # noqa: PLR0911
        if request.headers.get("Authorization") != f"Bearer {BOUNCY_FAKE_KEY}":
            return FakeReply.json_body(
                {"error": "Unauthorized", "message": "Invalid API key"},
                401,
            )
        method, path = request.method or "GET", _path(request).removeprefix("/v1")
        with self._state() as state:
            state.calls.append(f"{method} {path}")
            if path == "/links" and method == "GET":
                return self._list(state, _query(request, BouncyPage))
            if path == "/links" and method == "POST":
                return self._create(state, _body(request, BouncyCreate))
            if path == "/account/limits":
                return self._limits(state)
            if match := re.fullmatch(r"/analytics/links/(.+)", path):
                return self._analytics(state, match[1], _query(request, BouncyRange))
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
    def _list(state: FakeAccount, query: BouncyPage) -> FakeReply:
        page, limit = query.page, query.limit
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
            },
        )

    def _create(self, state: FakeAccount, body: BouncyCreate) -> FakeReply:
        domain = body.domain
        slug = body.slug.lower()
        if any(
            x["slug"] == slug and x["domain"] == domain for x in state.links.values()
        ):
            return FakeReply.json_body(
                {"error": "Custom slug or domain already taken"},
                400,
            )
        if len(state.links) >= state.limit:
            return FakeReply.json_body(
                {
                    "error": "You have reached the maximum number of deeplinks",
                    "code": "TOTAL_LINK_LIMIT_REACHED",
                },
                403,
            )
        link = self._new(state, slug, body.url, domain)
        return FakeReply.json_body(
            {"success": True, "data": link.model_dump(by_alias=True)},
            201,
        )

    def _link(
        self,
        state: FakeAccount,
        method: str,
        link_id: str,
        request: requests.PreparedRequest,
    ) -> FakeReply:
        if link_id not in state.links:
            return FakeReply.json_body(
                {"error": "Not Found", "message": "Deeplink not found"},
                404,
            )
        if method == "DELETE":
            del state.links[link_id]
            state.adult = [x for x in state.adult if x != link_id]
            return FakeReply.json_body({"success": True, "id": link_id})
        if method == "PUT":
            body = _body(request, BouncyUpdate).model_dump()
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
                },
            )
        return FakeReply.json_body({"success": True, "data": state.links[link_id]})

    @staticmethod
    def _analytics(state: FakeAccount, link_id: str, query: BouncyRange) -> FakeReply:
        if link_id not in state.links:
            return FakeReply.json_body({"error": "Not Found"}, 404)
        days = _days(query.first, query.last)
        if len(days) == 1:
            [day] = days
            buckets = [
                {"date": f"{day} {h:02}", "views": n.views, "clicks": n.clicks}
                for h in range(24)
                for n in [state.hour_numbers(link_id, day, h)]
                if n.views or n.clicks
            ]
            return FakeReply.json_body({"id": link_id, "dailyViews": buckets})
        daily = [
            {"date": d.isoformat(), "views": n.views, "clicks": n.clicks}
            for d in days
            for n in [state.numbers(link_id, d)]
            if n.views or n.clicks
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


def routes() -> list[FakeRoute]:
    def oopsie(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_oopsie().handle(request)

    def bouncy(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_bouncy().handle(request)

    return [
        FakeRoute(host="oopsie.bio", handler=oopsie),
        FakeRoute(host="api.bouncy.ai", handler=bouncy),
    ]
