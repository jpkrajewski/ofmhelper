# Copyright (C) 2026 jpkrajewski

import time
from urllib.parse import urlsplit
lazy from collections.abc import Callable
lazy from datetime import date

from pydantic import BaseModel

from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.chats.schema import ChatPlatform
from ofmhelpers.db.report_runs import store as report_runs
from ofmhelpers.db.report_runs.schema import DayCount
from ofmhelpers.db.short_links import store as link_store
from ofmhelpers.db.short_links.schema import LinkSyncKey, ctr
from ofmhelpers.db.tracked_accounts.store import list_accounts
from ofmhelpers.db.users import store as users
from ofmhelpers.features.events import Event, emit, listening
from ofmhelpers.features.links import keys as link_keys
from ofmhelpers.features.links.sync import sync_links
from ofmhelpers.features.reports import clicks, hours, mass
from ofmhelpers.features.reports.fan_sites import fan_sites
from ofmhelpers.features.reports.format import (
    NONE,
    compact,
    money,
    percent,
    window_title,
)
from ofmhelpers.features.reports.schedule import Due, due, local
from ofmhelpers.integrations.senders.schema import MAX_TEXT
from ofmhelpers.integrations.short_links.providers import link_providers
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.report_runs.schema import ReportRun
lazy from ofmhelpers.db.short_links.models import ShortLink

logger = get_logger(__name__)

NAME = "daily"
_FAILED_KEY = "reports:daily:failed"
_FAILED_S = 1800
_FAN_PLATFORMS = {p.value: p for p in (ChatPlatform.FANVUE, ChatPlatform.STACKED)}
_MAX_LINKS = 10


class ModelRow(BaseModel):
    name: str
    has_links: bool = False
    views: int = 0
    clicks: int = 0
    links: list[tuple[str, int]] = []
    subs: int | None = None
    cents: int | None = None

    def add_fan(self, subs: int, cents: int) -> None:
        self.subs = (self.subs or 0) + subs
        self.cents = (self.cents or 0) + cents

    def _link_lines(self) -> list[str]:
        ranked = sorted(self.links, key=lambda link: (-link[1], link[0]))
        lines = [f"- {address}: {compact(n)}" for address, n in ranked[:_MAX_LINKS]]
        if len(ranked) > _MAX_LINKS:
            lines.append(f"- and {len(ranked) - _MAX_LINKS} more")
        return lines

    def lines(self) -> list[str]:
        has_clicks = self.has_links
        conversion = (
            self.subs / self.clicks * 100
            if self.subs is not None and has_clicks and self.clicks
            else None
        )
        return [
            f"{self.name}:",
            f"Clicks: {compact(self.clicks) if has_clicks else NONE}",
            *self._link_lines(),
            f"Subs: {compact(self.subs)}",
            f"CTR: {percent(ctr(self.views, self.clicks)) if has_clicks else NONE}",
            f"Conversion rate: {percent(conversion)}",
            f"Sales: {money(self.cents)}",
        ]


def _profile(link: ShortLink) -> str | None:
    first = urlsplit(link.destination).path.strip("/").split("/")[0]
    return first.removeprefix("@") or None


class _Fan(BaseModel, frozen=True):
    platform: ChatPlatform
    username: str


def _fan(platform: ChatPlatform, username: str) -> _Fan:
    return _Fan(platform=platform, username=username.casefold())


def _model_rows(  # noqa: C901
    links: list[ShortLink],
    gained: Callable[[ShortLink], DayCount],
    since: float,
    until: float,
) -> list[ModelRow]:
    rows: dict[str, ModelRow] = {}
    named: set[str] = set()
    owner: dict[_Fan, str] = {}
    for link in links:
        profile = _profile(link)
        name = link.creator or profile or "Other"
        if link.creator:
            named.add(name)
        row = rows.setdefault(name, ModelRow(name=name))
        got = gained(link)
        row.has_links = True
        row.views += got.views
        row.clicks += got.clicks
        if got.clicks > 0:
            row.links.append((f"{link.domain}/{link.slug}", got.clicks))
        platform = _FAN_PLATFORMS.get(link.target or "")
        if platform is not None and profile:
            fan = _fan(platform, profile)
            if fan not in owner or (name in named and owner[fan] not in named):
                owner[fan] = name
    sites = {site.platform: site for site in fan_sites(since, until)}
    usernames: dict[_Fan, str] = {}
    for site in sites.values():
        for username in site.models():
            fan = _fan(site.platform, username)
            usernames[fan] = username
            if fan not in owner:
                owner[fan] = f"@{username}"
                rows.setdefault(owner[fan], ModelRow(name=owner[fan]))
    by_username = sorted(owner.items(), key=lambda item: item[0].username)
    for fan, name in sorted(by_username, key=lambda item: item[0].platform):
        found = sites[fan.platform].stats(usernames.get(fan, fan.username))
        if found is not None:
            rows[name].add_fan(found.subs, found.cents)
    return sorted(rows.values(), key=lambda r: (-r.clicks, -(r.cents or 0), r.name))


_NO_MODEL = "No model"


def _views_blocks() -> list[str]:
    per_model: dict[str, list[tuple[str, int]]] = {}
    for account in list_accounts(users.agency_id()):
        change = account.views(1)
        if change is not None:
            link = account.profile_url.rstrip("/")
            per_model.setdefault(account.creator or _NO_MODEL, []).append(
                (link, change.views),
            )
    blocks = []
    for name, found in sorted(
        per_model.items(),
        key=lambda item: (-sum(v for _, v in item[1]), item[0]),
    ):
        found.sort(key=lambda pair: (-pair[1], pair[0]))
        total = sum(views for _, views in found)
        lines = [f"{name} (Total: {compact(total)}):"]
        lines += [f"{link}: {compact(views)}" for link, views in found]
        blocks.append("\n".join(lines))
    return blocks or ["No account scanned in the last 24h"]


def _pieces(chunk: str) -> list[str]:
    if len(chunk) <= MAX_TEXT:
        return [chunk]
    pieces: list[list[str]] = [[]]
    for line in chunk.split("\n"):
        if pieces[-1] and len("\n".join([*pieces[-1], line])) > MAX_TEXT:
            pieces.append([])
        pieces[-1].append(line)
    return ["\n".join(piece) for piece in pieces]


def _events(
    name: str,
    title: str,
    chunks: list[str],
    key: str | None = None,
    replaces: str | None = None,
) -> list[Event]:
    parts: list[list[str]] = [[]]
    for chunk in (piece for whole in chunks for piece in _pieces(whole)):
        if parts[-1] and len("\n\n".join([*parts[-1], chunk])) > MAX_TEXT:
            parts.append([])
        parts[-1].append(chunk)
    many = len(parts) > 1
    return [
        Event(
            name=name,
            title=f"{title} ({n}/{len(parts)}):" if many else f"{title}:",
            text="\n\n".join(part),
            key=f"{key}:{n}" if key else None,
            replaces=(replaces if n == 1 else f"{replaces}:{n}") if replaces else None,
        )
        for n, part in enumerate(parts, start=1)
    ]


class _ModelsBlock(BaseModel, frozen=True):
    title: str
    chunks: list[str]


def _models_block(
    links: list[ShortLink],
    previous: ReportRun,
    now: float,
) -> _ModelsBlock:
    window = window_title(local(previous.ran_at), local(now))
    rows = _model_rows(
        links,
        lambda link: clicks.gained(link, previous.marks, previous.ran_at),
        previous.ran_at,
        now,
    )
    return _ModelsBlock(title=f"Clicks / Rev ({window})", chunks=_model_chunks(rows))


def _model_chunks(rows: list[ModelRow]) -> list[str]:
    return ["\n".join(row.lines()) for row in rows] or ["No models yet"]


def build(links: list[ShortLink], previous: ReportRun, now: float) -> list[Event]:
    day = local(now).date().isoformat()
    models_block = _models_block(links, previous, now)
    title, models = models_block.title, models_block.chunks

    def events(
        block: str,
        title: str,
        chunks: list[str],
        replaces: str | None = None,
    ) -> list[Event]:
        return _events(
            f"reports.daily.{block}",
            title,
            chunks,
            f"reports.daily:{day}:{block}",
            replaces,
        )

    return [
        *events("models", title, models),
        *events("views", "Views (last 24H)", _views_blocks()),
        *events(
            "chatting",
            "Chatting",
            ["\n".join(mass.lines(previous.ran_at, now))],
            mass.SLOT,
        ),
    ]


def money_now(now: float | None = None) -> list[Event]:
    now = time.time() if now is None else now
    owner = users.agency_id()
    links = link_store.list_links(owner)
    read = hours.last_day(links, owner, now)
    rows = _model_rows(
        links,
        lambda link: read.counts.get(link.id, DayCount()),
        hours.window_start(now).timestamp(),
        now,
    )
    chunks = _model_chunks(rows)
    chunks += [f"{label}: clicks could not be read right now" for label in read.unread]
    return _events("reports.money", "Clicks / Rev (Last 24H)", chunks)


def _fresh_links(owner: int) -> list[ShortLink]:
    keyed = [
        LinkSyncKey(user_id=owner, provider=p)
        for p in link_providers()
        if link_keys.api_key(owner, p)
    ]
    if settings.links.syncing_in_background(settings.infra.production):
        link_store.mark_due(owner)
        for key in link_store.claim_due(keyed):
            sync_links(key.user_id, key.provider)
    return link_store.list_links(owner)


def run(now: float | None = None) -> Due:
    now = time.time() if now is None else now
    if not listening() or get_text(_FAILED_KEY):
        return Due.NOTHING
    last = report_runs.last_run(NAME)
    planned = due(now, last)
    if planned.action == Due.NOTHING:
        return planned.action
    try:
        return _run(now, last, planned.action, planned.day)
    except Exception:
        set_text(_FAILED_KEY, "1", _FAILED_S)
        raise


def _run(now: float, last: ReportRun | None, action: Due, day: date) -> Due:
    links = _fresh_links(users.agency_id())
    marks = clicks.marks(links, now)
    if action == Due.BASELINE or last is None:
        report_runs.claim(NAME, day, now, sent=False, marks=marks)
        logger.info("daily report: baseline taken for %s", day)
        return Due.BASELINE
    events = build(links, last, now)
    if not report_runs.claim(NAME, day, now, sent=True, marks=marks):
        return Due.NOTHING
    for event in events:
        emit(event)
    logger.info("daily report for %s: %d events", day, len(events))
    return action
