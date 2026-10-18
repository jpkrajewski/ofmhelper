"""
/reels: a watch list of Instagram profiles, each in an optional group
("asian", "gay", ...), and one feed of all their recent reels past
`min_views`, at most `max_duration_s` long, most viewed first -- filtered by
group, paged `feed_page_size` at a time (static/js/gallery-scroll.js loads
the rest). "Clone selected" downloads the picked reels into the asset store
and sends the user to /clone with them already in the videos picker;
"Download selected" hands them straight back (one mp4, or a zip of several).

Adding profiles queues their first scan on the worker (with any other
profile due for its 12 h rescan, web/reel_watch.py), so no request waits on
Instagram; the page polls `GET /reels/watchlist` until none is pending.

Reels play on the page straight from Instagram's CDN (it allows cross-origin
embedding); nothing is downloaded until Clone or Download selected.
"""

import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, TypeAdapter, ValidationError
from starlette.background import BackgroundTask

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
from ofmhelpers.web.schemas import FeedItem, GroupForm, Leaderboard, WatchForm
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
# A follower multiple: only reels with at least that many times their
# account's followers in views.
RatioQuery = Annotated[int | None, Query(ge=1, le=1000)]

# The follower multiples offered as chips.
_RATIO_STEPS = (2, 3, 5, 10)


class _Filters(BaseModel):
    """The feed's filters. Every chip link keeps the other two."""

    group: str | None = None
    rank: str | None = None
    ratio: int | None = None

    def includes(self, item: FeedItem) -> bool:
        """Whether the feed shows `item` under these filters."""
        return (not self.rank or item.reel.tier == self.rank) and (
            not self.ratio or item.reel.ratio >= self.ratio
        )

    def url(self, path: str, **changes: str | int | None) -> str:
        """`path` with these filters, `changes` applied, as its query."""
        query = {
            k: v for k, v in self.model_copy(update=changes).model_dump().items() if v
        }
        return path + (f"?{urlencode(query)}" if query else "")


def _feed_page(filters: _Filters, offset: int) -> tuple[list[FeedItem], int | None]:
    """One page of the feed plus the next page's offset, None on the last --
    the absence of a next offset is what ends the scroll. The filters apply
    before paging, so every page holds only reels they keep."""
    size = settings.instagram.feed_page_size
    feed = [
        item
        for item in FeedItem.of(list_watched(filters.group))
        if filters.includes(item)
    ]
    next_offset = offset + size
    return feed[offset:next_offset], next_offset if next_offset < len(feed) else None


def _group_links(filters: _Filters) -> list[tuple[str, str, bool]]:
    """The feed's group chips (label, href, current)."""
    return [("All", filters.url("/reels", group=None), not filters.group)] + [
        (g, filters.url("/reels", group=g), g == filters.group) for g in list_groups()
    ]


def _rank_links(filters: _Filters) -> list[tuple[str, str | None, str, bool]]:
    """The feed's rank chips (label, tier, href, current), best rank first,
    each labelled with its view range top-down ("1M-500k")."""
    ceilings = [None] + [floor for floor, _ in VIEW_TIERS[:-1]]
    return [("All", None, filters.url("/reels", rank=None), not filters.rank)] + [
        (
            f"{name} {_short(floor)}+"
            if ceiling is None
            else f"{name} {_short(ceiling)}\N{EN DASH}{_short(floor)}",
            name,
            filters.url("/reels", rank=name),
            name == filters.rank,
        )
        for (floor, name), ceiling in zip(VIEW_TIERS, ceilings, strict=True)
    ]


def _ratio_links(filters: _Filters) -> list[tuple[str, str, bool]]:
    """The feed's follower-multiple chips (label, href, current)."""
    return [("Any", filters.url("/reels", ratio=None), not filters.ratio)] + [
        (
            f"\N{MULTIPLICATION SIGN}{n}+",
            filters.url("/reels", ratio=n),
            n == filters.ratio,
        )
        for n in _RATIO_STEPS
    ]


_MILLION = 1_000_000


def _short(views: int) -> str:
    """1_000_000 -> "1M", 200_000 -> "200k"."""
    return f"{views // _MILLION}M" if views >= _MILLION else f"{views // 1000}k"


@router.get("")
def form(
    request: Request,
    group: GroupQuery = None,
    rank: RankQuery = None,
    ratio: RatioQuery = None,
):
    filters = _Filters(group=group or None, rank=rank or None, ratio=ratio)
    feed, next_offset = _feed_page(filters, 0)
    return render(
        request,
        "reels.html",
        board=Leaderboard.of(list_watched(filters.group)),
        feed=feed,
        next_offset=next_offset,
        feed_endpoint=filters.url("/reels/feed"),
        group_links=_group_links(filters),
        rank_links=_rank_links(filters),
        ratio_links=_ratio_links(filters),
        groups=list_groups(),
        group=group or "",
        rank=rank or "",
        ratio=ratio,
        max_videos=settings.web.clone_max_videos,
    )


@router.get("/feed")
def feed_page(
    request: Request,
    group: GroupQuery = None,
    rank: RankQuery = None,
    ratio: RatioQuery = None,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """The next page of cards, as the fragment gallery-scroll.js appends."""
    filters = _Filters(group=group or None, rank=rank or None, ratio=ratio)
    feed, next_offset = _feed_page(filters, offset)
    return render(request, "_reels_feed_page.html", feed=feed, next_offset=next_offset)


@router.get("/watchlist")
def watchlist(request: Request, group: GroupQuery = None):
    """The watch list rows, as the page polls them while scans run."""
    return render(
        request,
        "_reels_watch_rows.html",
        board=Leaderboard.of(list_watched(group or None)),
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


def _download_picked(reels: str) -> list[Path]:
    """The picked reels (the page's JSON) saved as mp4s in a fresh job dir."""
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
            return list(pool.map(lambda r: _download(client, r, job_dir), picked))
    except (OSError, requests.RequestException) as exc:
        logger.warning("reel download failed", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Could not download a reel — rescan the profile and retry",
        ) from exc


@router.post("/download")
def download(reels: Annotated[str, Form()]):
    """The picked reels as a file: the mp4 itself for one, a zip for more
    (stored, not deflated: H.264 does not compress further). Nothing is
    kept: the job dir goes once the response is sent."""
    files = _download_picked(reels)
    job_dir = files[0].parent
    cleanup = BackgroundTask(shutil.rmtree, job_dir, ignore_errors=True)
    if len(files) == 1:
        return FileResponse(
            files[0], media_type="video/mp4", filename=files[0].name, background=cleanup
        )
    archive = job_dir / "reels.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        for file in files:
            zf.write(file, file.name)
    return FileResponse(
        archive, media_type="application/zip", filename=archive.name, background=cleanup
    )


@router.post("/clone")
def clone(request: Request, reels: Annotated[str, Form()]):
    """Downloads the picked reels into the asset store and answers with the
    /clone URL that opens with them picked."""
    files = _download_picked(reels)
    assets_root = assets_root_for(request)
    paths = [
        str(path)
        for file in files
        if (path := register_generated_asset(file, assets_root)) is not None
    ]
    return {"redirect": "/clone?" + urlencode([("video", p) for p in paths])}
