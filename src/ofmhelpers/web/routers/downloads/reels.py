"""
/reels: a watch list of Instagram profiles, each in an optional group
("asian", "gay", ...), and one feed of all their recent reels past
`min_views`, at most `max_duration_s` long, most viewed first -- filtered by
group, paged `feed_page_size` at a time (static/js/gallery-scroll.js loads
the rest). "Clone selected" downloads the picked reels into the asset store
and sends the user to /clone with them already in the videos picker.

Adding profiles queues their first scan on the worker (with any other
profile due for its 12 h rescan, web/reel_watch.py), so no request waits on
Instagram; the page polls `GET /reels/watchlist` until none is pending.

Reels play on the page straight from Instagram's CDN (it allows cross-origin
embedding); nothing is downloaded until Clone selected.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Form, HTTPException, Query, Request
from pydantic import TypeAdapter, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.instagram import (
    VIEW_TIERS,
    InstagramClient,
    ProfileHandle,
    ReelDownload,
)
from ofmhelpers.log import get_logger
from ofmhelpers.web.reel_watch import queue_due_scans
from ofmhelpers.web.routers.task_helpers import (
    UPLOADS_ROOT,
    assets_root_for,
    make_job_dir,
    register_generated_asset,
)
from ofmhelpers.web.schemas import FeedItem, GroupForm, WatchedProfile, WatchForm
from ofmhelpers.web.stores.watched_profiles import (
    create_group,
    list_groups,
    list_watched,
    set_group,
    unwatch,
    watch,
)
from ofmhelpers.web.templates_config import render

logger = get_logger(__name__)

router = APIRouter(prefix="/reels", tags=["reels"])

_DOWNLOADS = TypeAdapter(list[ReelDownload])

GroupQuery = Annotated[str | None, Query(max_length=64)]
# A rank (VIEW_TIERS name): the feed narrowed to exactly that rank.
RankQuery = Annotated[str | None, Query(max_length=16)]


def _feed_page(
    group: str | None, rank: str | None, offset: int
) -> tuple[list[FeedItem], int | None]:
    """One page of the feed plus the next page's offset, None on the last --
    the absence of a next offset is what ends the scroll. The filters apply
    before paging, so every page holds only that group's reels of that rank."""
    size = settings.instagram.feed_page_size
    feed = [
        i
        for i in FeedItem.of(list_watched(group or None))
        if not rank or i.reel.tier == rank
    ]
    next_offset = offset + size
    return feed[offset:next_offset], next_offset if next_offset < len(feed) else None


def _url(path: str, group: str | None, rank: str | None) -> str:
    query = {k: v for k, v in (("group", group), ("rank", rank)) if v}
    return path + (f"?{urlencode(query)}" if query else "")


def _group_links(group: str | None, rank: str | None) -> list[tuple[str, str, bool]]:
    """The feed's group chips (label, href, current), keeping the rank."""
    return [("All groups", _url("/reels", None, rank), not group)] + [
        (g, _url("/reels", g, rank), g == group) for g in list_groups()
    ]


def _rank_links(
    group: str | None, rank: str | None
) -> list[tuple[str, str | None, str, bool]]:
    """The feed's rank chips (label, tier, href, current), best rank first,
    each labelled with its view range, keeping the group."""
    ceilings = [None] + [floor for floor, _ in VIEW_TIERS[:-1]]
    return [("All ranks", None, _url("/reels", group, None), not rank)] + [
        (
            f"{name} {_short(floor)}+"
            if ceiling is None
            else f"{name} {_short(floor)}\N{EN DASH}{_short(ceiling)}",
            name,
            _url("/reels", group, name),
            name == rank,
        )
        for (floor, name), ceiling in zip(VIEW_TIERS, ceilings, strict=True)
    ]


_MILLION = 1_000_000


def _short(views: int) -> str:
    """1_000_000 -> "1M", 200_000 -> "200k"."""
    return f"{views // _MILLION}M" if views >= _MILLION else f"{views // 1000}k"


@router.get("")
def form(request: Request, group: GroupQuery = None, rank: RankQuery = None):
    s = settings.instagram
    profiles = WatchedProfile.by_reels(list_watched(group or None))
    feed, next_offset = _feed_page(group, rank, 0)
    return render(
        request,
        "reels.html",
        profiles=profiles,
        top_profile=WatchedProfile.most_viewed(profiles),
        feed=feed,
        next_offset=next_offset,
        feed_endpoint=_url("/reels/feed", group, rank),
        group_links=_group_links(group, rank),
        rank_links=_rank_links(group, rank),
        groups=list_groups(),
        group=group or "",
        rank=rank or "",
        min_views=s.min_views,
        max_duration_s=s.max_duration_s,
        max_age_days=s.max_age_days,
        rescan_hours=s.rescan_interval_s // 3600,
        max_videos=settings.web.clone_max_videos,
    )


@router.get("/feed")
def feed_page(
    request: Request,
    group: GroupQuery = None,
    rank: RankQuery = None,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """The next page of cards, as the fragment gallery-scroll.js appends."""
    feed, next_offset = _feed_page(group, rank, offset)
    return render(request, "_reels_feed_page.html", feed=feed, next_offset=next_offset)


@router.get("/watchlist")
def watchlist(request: Request, group: GroupQuery = None):
    """The watch list rows, as the page polls them while scans run."""
    profiles = WatchedProfile.by_reels(list_watched(group or None))
    return render(
        request,
        "_reels_watch_rows.html",
        profiles=profiles,
        top_profile=WatchedProfile.most_viewed(profiles),
        groups=list_groups(),
    )


@router.post("/groups")
def new_group(name: Annotated[str, Form()] = ""):
    """ "+ New group": a group every dropdown offers before any profile is in
    it. Answers the stored name (trimmed, lower case)."""
    group = GroupForm(group=name).group
    if not group:
        raise HTTPException(status_code=400, detail="Name the group")
    create_group(group)
    return {"group": group}


@router.post("/watch")
def add(profiles: Annotated[str, Form()] = "", group: Annotated[str, Form()] = ""):
    """Adds every profile on its own line to `group` and queues the first
    scan of the new ones. A profile already watched is only moved to
    `group` (when one is given) and left to its 12 h rescan. One bad line
    rejects the lot, before anything is added."""
    form = WatchForm(profiles=profiles, group=group)
    try:
        usernames = form.usernames
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not usernames:
        raise HTTPException(status_code=400, detail="Add at least one profile")
    added = [u for u in usernames if watch(u, form.group or None)]
    queue_due_scans()
    return {"added": added, "already_watched": len(usernames) - len(added)}


def _handle(username: str) -> str:
    try:
        return ProfileHandle(username=username).username
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Not an Instagram profile") from exc


@router.post("/group/{username}")
def move(username: str, group: Annotated[str, Form()] = ""):
    """A watch-list row's group dropdown: moves the profile ("" = no group)."""
    handle = _handle(username)
    target = GroupForm(group=group).group
    if not set_group(handle, target or None):
        raise HTTPException(status_code=404, detail=f"@{handle} is not watched")
    return {"username": handle, "group": target}


@router.post("/unwatch/{username}")
def remove(username: str):
    handle = _handle(username)
    if not unwatch(handle):
        raise HTTPException(status_code=404, detail=f"@{handle} is not watched")
    return {"removed": handle}


def _download(client: InstagramClient, reel: ReelDownload, job_dir: Path) -> Path:
    return client.download_video(str(reel.video_url), job_dir / f"{reel.code}.mp4")


@router.post("/clone")
def clone(request: Request, reels: Annotated[str, Form()]):
    """Downloads the picked reels into the asset store and answers with the
    /clone URL that opens with them picked."""
    try:
        picked = _DOWNLOADS.validate_json(reels)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid reel selection") from exc
    max_videos = settings.web.clone_max_videos
    if not 1 <= len(picked) <= max_videos:
        raise HTTPException(
            status_code=400, detail=f"Pick between 1 and {max_videos} reels"
        )

    client = InstagramClient()
    job_dir = make_job_dir(UPLOADS_ROOT / "reels")
    try:
        with ThreadPoolExecutor(settings.instagram.download_workers) as pool:
            files = list(pool.map(lambda r: _download(client, r, job_dir), picked))
    except (OSError, requests.RequestException) as exc:
        logger.warning("reel download failed", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Could not download a reel — rescan the profile and retry",
        ) from exc

    assets_root = assets_root_for(request)
    paths = [
        str(path)
        for file in files
        if (path := register_generated_asset(file, assets_root)) is not None
    ]
    return {"redirect": "/clone?" + urlencode([("video", p) for p in paths])}
