"""What /reels shows and does behind its routes
(web/routers/downloads/reels.py): the watch list and its groups, the one feed
of every watched profile's reels under the page's filters, and the picked
reels downloaded -- handed back as a file or put into the asset store for
/clone."""

import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode

import requests
from pydantic import BaseModel, TypeAdapter, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.db.stores.watched_profiles import (
    create_group,
    list_groups,
    list_watched,
    set_group,
    unwatch,
    watch,
)
from ofmhelpers.features.assets.store import (
    make_job_dir,
    register_generated_asset,
    uploads_root,
)
from ofmhelpers.features.downloads.service import Redirect
from ofmhelpers.features.reels.thumbs import reel_thumb, thumb_path
from ofmhelpers.features.reels.watch import queue_due_scans
from ofmhelpers.features.refusals import (
    NotFoundError,
    RefusedError,
    UnavailableError,
)
from ofmhelpers.integrations.instagram import (
    VIEW_TIERS,
    InstagramClient,
    ProfileHandle,
    ReelDownload,
)
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import FeedItem, GroupForm, Leaderboard, WatchForm
from ofmhelpers.telemetry import in_this_trace

logger = get_logger(__name__)

_DOWNLOADS = TypeAdapter(list[ReelDownload])

# The follower multiples offered as chips.
_RATIO_STEPS = (2, 3, 5, 10)

_MILLION = 1_000_000


class WatchAdded(BaseModel):
    added: list[str]
    already_watched: int


class ProfileMoved(BaseModel):
    username: str
    group: str


class Filters(BaseModel):
    """The feed's filters. Every chip link keeps the other two."""

    group: str | None = None
    rank: str | None = None
    ratio: int | None = None

    def includes(self, item: FeedItem) -> bool:
        """Whether the feed shows `item` under these filters; never a reel
        past `max_duration_s`, even one stored by an older scan."""
        length = item.reel.duration_s
        return (
            (length is None or length <= settings.instagram.max_duration_s)
            and (not self.rank or item.reel.tier == self.rank)
            and (not self.ratio or item.reel.ratio >= self.ratio)
        )

    def url(self, path: str, **changes: str | int | None) -> str:
        """`path` with these filters, `changes` applied, as its query."""
        query = {
            k: v for k, v in self.model_copy(update=changes).model_dump().items() if v
        }
        return path + (f"?{urlencode(query)}" if query else "")


class FeedPage(BaseModel):
    """One page of the feed plus the next page's offset, None on the last --
    the absence of a next offset is what ends the scroll."""

    feed: list[FeedItem]
    next_offset: int | None


class WatchRows(BaseModel):
    """The watch list as its rows render it."""

    board: Leaderboard
    groups: list[str]


class PageView(FeedPage, WatchRows):
    """/reels: each field is a name `reels.html` reads. The chips are
    (label, href, current) -- the rank ones (label, tier, href, current)."""

    feed_endpoint: str
    group_links: list[tuple[str, str, bool]]
    rank_links: list[tuple[str, str | None, str, bool]]
    ratio_links: list[tuple[str, str, bool]]
    group: str
    rank: str
    ratio: int | None
    max_videos: int


def _filters(group: str | None, rank: str | None, ratio: int | None) -> Filters:
    return Filters(group=group or None, rank=rank or None, ratio=ratio)


def _feed_page(filters: Filters, offset: int, owner: int | None) -> FeedPage:
    """The filters apply before paging, so every page holds only reels they
    keep."""
    size = settings.instagram.feed_page_size
    feed = [
        item
        for item in FeedItem.of(list_watched(filters.group, owner))
        if filters.includes(item)
    ]
    next_offset = offset + size
    return FeedPage(
        feed=feed[offset:next_offset],
        next_offset=next_offset if next_offset < len(feed) else None,
    )


def _group_links(filters: Filters, owner: int | None) -> list[tuple[str, str, bool]]:
    """The feed's group chips (label, href, current)."""
    return [("All", filters.url("/reels", group=None), not filters.group)] + [
        (g, filters.url("/reels", group=g), g == filters.group)
        for g in list_groups(owner)
    ]


def _short(views: int) -> str:
    """1_000_000 -> "1M", 200_000 -> "200k"."""
    return f"{views // _MILLION}M" if views >= _MILLION else f"{views // 1000}k"


def _rank_links(filters: Filters) -> list[tuple[str, str | None, str, bool]]:
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


def _ratio_links(filters: Filters) -> list[tuple[str, str, bool]]:
    """The feed's follower-multiple chips (label, href, current)."""
    return [("Any", filters.url("/reels", ratio=None), not filters.ratio)] + [
        (
            f"\N{MULTIPLICATION SIGN}{n}+",
            filters.url("/reels", ratio=n),
            n == filters.ratio,
        )
        for n in _RATIO_STEPS
    ]


def page_view(
    scope: int, group: str | None, rank: str | None, ratio: int | None
) -> PageView:
    filters = _filters(group, rank, ratio)
    first = _feed_page(filters, 0, scope)
    return PageView(
        board=Leaderboard.of(list_watched(filters.group, scope)),
        feed=first.feed,
        next_offset=first.next_offset,
        feed_endpoint=filters.url("/reels/feed"),
        group_links=_group_links(filters, scope),
        rank_links=_rank_links(filters),
        ratio_links=_ratio_links(filters),
        groups=list_groups(scope),
        group=group or "",
        rank=rank or "",
        ratio=ratio,
        max_videos=settings.web.clone_max_videos,
    )


def feed_page(
    scope: int, group: str | None, rank: str | None, ratio: int | None, offset: int
) -> FeedPage:
    """The next page of cards."""
    return _feed_page(_filters(group, rank, ratio), offset, scope)


def cover(code: str, scope: int) -> Path:
    """A feed card's cover, small and kept (`thumbs.py`). Only reels of the
    profiles `scope` watches (the scan and the kept file are shared, who may
    fetch is not): one of somebody else's answers like one that is not
    there. The source URL comes from the stored scan, never from the
    request."""
    found = next(
        (i.reel for i in FeedItem.of(list_watched(None, scope)) if i.reel.code == code),
        None,
    )
    path: Path | None = thumb_path(code) if found else None
    if found is not None and (path is None or not path.is_file()):
        path = reel_thumb(code, str(found.thumbnail_url))
    if path is None:
        msg = "No thumbnail"
        raise NotFoundError(msg)
    return path


def watch_rows(scope: int, group: str | None) -> WatchRows:
    return WatchRows(
        board=Leaderboard.of(list_watched(group or None, scope)),
        groups=list_groups(scope),
    )


def new_group(name: str, owner_id: int) -> str:
    """A group every dropdown offers before any profile is in it: its stored
    name (trimmed, lower case)."""
    group = GroupForm(group=name).group
    if not group:
        msg = "Name the group"
        raise RefusedError(msg)
    create_group(group, owner_id)
    return group


def add(profiles: str, group: str, owner_id: int) -> WatchAdded:
    """Adds every profile on its own line to `group` and queues the first
    scan of the new ones. A profile already watched is only moved to
    `group` (when one is given) and left to its rescan, if rescans are on
    (`watch.py`). One bad line rejects the lot, before anything is added."""
    form = WatchForm(profiles=profiles, group=group)
    try:
        usernames = form.usernames
    except ValueError as exc:
        # `WatchForm.usernames` words its own refusal (the lines that are no
        # profile): that sentence is the page's.
        raise RefusedError(str(exc)) from exc
    if not usernames:
        msg = "Add at least one profile"
        raise RefusedError(msg)
    added = [u for u in usernames if watch(u, form.group or None, user_id=owner_id)]
    queue_due_scans()
    return WatchAdded(added=added, already_watched=len(usernames) - len(added))


def _handle(username: str) -> str:
    try:
        return ProfileHandle(username=username).username
    except ValidationError as exc:
        msg = "Not an Instagram profile"
        raise RefusedError(msg) from exc


def move(username: str, group: str, owner_id: int) -> ProfileMoved:
    """A watch-list row's group dropdown: moves the profile ("" = no group)."""
    handle = _handle(username)
    target = GroupForm(group=group).group
    if not set_group(handle, target or None, owner_id):
        msg = f"@{handle} is not watched"
        raise NotFoundError(msg)
    return ProfileMoved(username=handle, group=target)


def remove(username: str, owner_id: int) -> str:
    """The handle no longer watched."""
    handle = _handle(username)
    if not unwatch(handle, owner_id):
        msg = f"@{handle} is not watched"
        raise NotFoundError(msg)
    return handle


# --- the picked reels ----------------------------------------------------------


class PickedFile(BaseModel):
    """The picked reels as one file to send; `folder` goes once it is sent."""

    path: Path
    media_type: str
    folder: Path

    def forget(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)


def _download(client: InstagramClient, reel: ReelDownload, job_dir: Path) -> Path:
    return client.download_video(str(reel.video_url), job_dir / f"{reel.code}.mp4")


def _picked(reels: str) -> list[ReelDownload]:
    try:
        picked = _DOWNLOADS.validate_json(reels)
    except ValidationError as exc:
        msg = "Invalid reel selection"
        raise RefusedError(msg) from exc
    max_videos = settings.web.clone_max_videos
    if not 1 <= len(picked) <= max_videos:
        msg = f"Pick between 1 and {max_videos} reels"
        raise RefusedError(msg)
    return picked


def _download_picked(reels: str) -> list[Path]:
    """The picked reels (the page's JSON) saved as mp4s in a fresh job dir."""
    picked = _picked(reels)
    client = InstagramClient()
    job_dir = make_job_dir(uploads_root() / "reels")
    try:
        with ThreadPoolExecutor(settings.instagram.download_workers) as pool:
            download = in_this_trace(lambda r: _download(client, r, job_dir))
            return list(pool.map(download, picked))
    except (OSError, requests.RequestException) as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.warning("reel download failed", exc_info=True)
        msg = "Could not download a reel — rescan the profile and retry"
        raise UnavailableError(msg) from exc
    except Exception:
        # Any other failure is still a crash, but leaves no folder behind.
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


def picked_file(reels: str) -> PickedFile:
    """The picked reels as a file: the mp4 itself for one, a zip for more
    (stored, not deflated: H.264 does not compress further)."""
    files = _download_picked(reels)
    job_dir = files[0].parent
    if len(files) == 1:
        return PickedFile(path=files[0], media_type="video/mp4", folder=job_dir)
    archive = job_dir / "reels.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        for file in files:
            zf.write(file, file.name)
    return PickedFile(path=archive, media_type="application/zip", folder=job_dir)


def clone(reels: str, assets_root: Path) -> Redirect:
    """Downloads the picked reels into the asset store and answers with the
    /clone URL that opens with them picked. The store keeps its own link to
    each file, so the job dir goes."""
    files = _download_picked(reels)
    try:
        paths = [
            str(path)
            for file in files
            if (path := register_generated_asset(file, assets_root)) is not None
        ]
    finally:
        shutil.rmtree(files[0].parent, ignore_errors=True)
    return Redirect(redirect="/clone?" + urlencode([("video", p) for p in paths]))
