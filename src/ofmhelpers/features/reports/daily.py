"""The daily report (`OFM_REPORTS_DAILY_AT`, 14:00 Warsaw): three events to the
senders, each covering the window since the previous report.

- Models: per model, the clicks and landing views its links got (`clicks.py`), CTR
  (clicks per landing view), and from its fan sites the new subscribers, the sales
  (gross) and the conversion rate (subscribers per click). A model is a `creators`
  name; links without one go by the profile they send to (`rachel.rosee`). Its fan
  sites are where its links send people (`fanvue.com/<name>`, `stacked.com/@<name>`);
  a Fanvue/Stacked model no link sends to gets a row of its own (`@walker_dane`).
- Views: per model (its accounts' `creators` name), the total and each Instagram
  account's link with its views of reels posted in the 24 h before its latest scan
  (the /accounts "Last 24 hours" number), as of the scan the tracker runs 10 min
  before the report (`OFM_ACCOUNTS_SCAN_TIMES`: 13:50).
- Chatting: who sent a mass message in the window (`OFM_CHATTING_EVENTS_PLATFORMS_OFF`
  platforms left out: Fanvue for now). Unanswered chats are told as they
  happen (`features/chatting/wait_events.py`), not here.

Whose: the agency's, the password account's links and accounts (`users.agency_id`);
chats have no owner (admin only), so every model there.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import BaseModel

from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats, report_runs, users
from ofmhelpers.db.stores import short_links as link_store
from ofmhelpers.db.stores.tracked_accounts import list_accounts
from ofmhelpers.features.events import Event, emit, listening
from ofmhelpers.features.links.sync import sync_links
from ofmhelpers.features.reports import clicks
from ofmhelpers.features.reports.fan_sites import fan_sites
from ofmhelpers.features.reports.format import (
    NONE,
    compact,
    money,
    percent,
    window_title,
)
from ofmhelpers.features.reports.schedule import Due, due, local
from ofmhelpers.integrations.short_links.providers import link_providers
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import ChatPlatform, DeliveryKind
from ofmhelpers.schemas.events import MAX_TEXT
from ofmhelpers.schemas.links import ShortLink, ctr

if TYPE_CHECKING:
    from datetime import date

    from ofmhelpers.schemas.reports import ReportRun

logger = get_logger(__name__)

NAME = "daily"
_FAILED_KEY = "reports:daily:failed"
_FAILED_S = 1800
_FAN_PLATFORMS = {p.value: p for p in (ChatPlatform.FANVUE, ChatPlatform.STACKED)}


class ModelRow(BaseModel):
    name: str
    has_links: bool = False
    views: int = 0
    clicks: int = 0
    subs: int | None = None
    cents: int | None = None

    def add_fan(self, subs: int, cents: int) -> None:
        self.subs = (self.subs or 0) + subs
        self.cents = (self.cents or 0) + cents

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
            f"Subs: {compact(self.subs)}",
            f"CTR: {percent(ctr(self.views, self.clicks)) if has_clicks else NONE}",
            f"Conversion rate: {percent(conversion)}",
            f"Sales: {money(self.cents)}",
        ]


def _profile(link: ShortLink) -> str | None:
    """The profile name a link sends to (`/walker_dane`, `/@danewalkerjohnson`)."""
    first = urlsplit(link.destination).path.strip("/").split("/")[0]
    return first.removeprefix("@") or None


_Fan = tuple[ChatPlatform, str]


def _fan(platform: ChatPlatform, username: str) -> _Fan:
    """A fan-site account, its name as any case (`@DaneWalker` = `danewalker`)."""
    return platform, username.casefold()


def _model_rows(
    links: list[ShortLink], previous: ReportRun, until: float
) -> list[ModelRow]:
    """One row per model; each fan-site account counted in one row only: a model's
    own (`creators` name) before a link without one, else the first seen."""
    rows: dict[str, ModelRow] = {}
    named: set[str] = set()
    owner: dict[_Fan, str] = {}
    for link in links:
        profile = _profile(link)
        name = link.creator or profile or "Other"
        if link.creator:
            named.add(name)
        row = rows.setdefault(name, ModelRow(name=name))
        got = clicks.gained(link, previous.marks, previous.ran_at)
        row.has_links = True
        row.views += got.views
        row.clicks += got.clicks
        platform = _FAN_PLATFORMS.get(link.target or "")
        if platform is not None and profile:
            fan = _fan(platform, profile)
            if fan not in owner or (name in named and owner[fan] not in named):
                owner[fan] = name
    sites = fan_sites(previous.ran_at, until)
    usernames: dict[_Fan, str] = {}
    for site in sites.values():
        for username in site.models():
            fan = _fan(site.platform, username)
            usernames[fan] = username
            if fan not in owner:
                owner[fan] = f"@{username}"
                rows.setdefault(owner[fan], ModelRow(name=owner[fan]))
    for fan, name in sorted(owner.items()):
        platform, key = fan
        found = sites[platform].stats(usernames.get(fan, key))
        if found is not None:
            rows[name].add_fan(found.subs, found.cents)
    return sorted(rows.values(), key=lambda r: (-r.clicks, -(r.cents or 0), r.name))


_NO_MODEL = "No model"


def _views_blocks() -> list[str]:
    """Per model (most views first): its total, then each account's link and
    views (most first). An account without a model goes under "No model"."""
    per_model: dict[str, list[tuple[str, int]]] = {}
    for account in list_accounts(users.agency_id()):
        change = account.views(1)
        if change is not None:
            link = account.profile_url.rstrip("/")
            per_model.setdefault(account.creator or _NO_MODEL, []).append(
                (link, change.views)
            )
    blocks = []
    for name, found in sorted(
        per_model.items(), key=lambda item: (-sum(v for _, v in item[1]), item[0])
    ):
        found.sort(key=lambda pair: (-pair[1], pair[0]))
        total = sum(views for _, views in found)
        lines = [f"{name} (Total: {compact(total)}):"]
        lines += [f"{link}: {compact(views)}" for link, views in found]
        blocks.append("\n".join(lines))
    return blocks or ["No account scanned in the last 24h"]


def _chatting_lines(since: float, until: float) -> list[str]:
    off = settings.chatting.events_off()
    sent = sorted(
        (
            t
            for t in chats.counts_between(DeliveryKind.MASS, since, until)
            if t.platform.value not in off
        ),
        key=lambda t: t.model_username,
    )
    if not sent:
        return ["Mass message sent: none"]
    return [
        f"Mass message sent: @{t.model_username}"
        + (f" ({t.count})" if t.count > 1 else "")
        for t in sent
    ]


def _pieces(chunk: str) -> list[str]:
    """`chunk` in pieces that fit a message, cut between lines, never in one."""
    if len(chunk) <= MAX_TEXT:
        return [chunk]
    pieces: list[list[str]] = [[]]
    for line in chunk.split("\n"):
        if pieces[-1] and len("\n".join([*pieces[-1], line])) > MAX_TEXT:
            pieces.append([])
        pieces[-1].append(line)
    return ["\n".join(piece) for piece in pieces]


def _events(block: str, title: str, chunks: list[str], day: str) -> list[Event]:
    """One event per block, in parts when it outgrows a message."""
    parts: list[list[str]] = [[]]
    for chunk in (piece for whole in chunks for piece in _pieces(whole)):
        if parts[-1] and len("\n\n".join([*parts[-1], chunk])) > MAX_TEXT:
            parts.append([])
        parts[-1].append(chunk)
    many = len(parts) > 1
    return [
        Event(
            name=f"reports.daily.{block}",
            title=f"{title} ({n}/{len(parts)}):" if many else f"{title}:",
            text="\n\n".join(part),
            key=f"reports.daily:{day}:{block}:{n}",
        )
        for n, part in enumerate(parts, start=1)
    ]


def build(links: list[ShortLink], previous: ReportRun, now: float) -> list[Event]:
    """The report's events for the window since `previous`."""
    day = local(now).date().isoformat()
    window = window_title(local(previous.ran_at), local(now))
    rows = _model_rows(links, previous, now)
    models = ["\n".join(row.lines()) for row in rows] or ["No models yet"]
    return [
        *_events("models", f"Clicks / Rev ({window})", models, day),
        *_events("views", "Views (last 24H)", _views_blocks(), day),
        *_events(
            "chatting",
            "Chatting",
            ["\n".join(_chatting_lines(previous.ran_at, now))],
            day,
        ),
    ]


def _fresh_links(owner: int) -> list[ShortLink]:
    """The owner's links, synced first so the counters are the providers' now:
    through the syncs' own claim, so never beside one a worker is running (that
    one's numbers are as fresh)."""
    keyed = [(owner, p) for p in link_providers() if link_store.api_key(owner, p)]
    link_store.mark_due(owner)
    for user_id, provider in link_store.claim_due(keyed):
        sync_links(user_id, provider)
    return link_store.list_links(owner)


def run(now: float | None = None) -> Due:
    """The sweep: a baseline the first time, the report when due, else nothing.
    Nobody to send to (no sender set up): nothing read or counted. A run that
    failed waits `_FAILED_S` before the next try (it calls the providers)."""
    now = time.time() if now is None else now
    if not listening() or get_text(_FAILED_KEY):
        return Due.NOTHING
    last = report_runs.last_run(NAME)
    action, day = due(now, last)
    if action == Due.NOTHING:
        return action
    try:
        return _run(now, last, action, day)
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
