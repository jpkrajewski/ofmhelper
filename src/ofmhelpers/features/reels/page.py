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

_RATIO_STEPS = (2, 3, 5, 10)

_MILLION = 1_000_000


class WatchAdded(BaseModel):
    added: list[str]
    already_watched: int


class ProfileMoved(BaseModel):
    username: str
    group: str


class Filters(BaseModel):
    group: str | None = None
    rank: str | None = None
    ratio: int | None = None

    def includes(self, item: FeedItem) -> bool:
        length = item.reel.duration_s
        return (
            (length is None or length <= settings.instagram.max_duration_s)
            and (not self.rank or item.reel.tier == self.rank)
            and (not self.ratio or item.reel.ratio >= self.ratio)
        )

    def url(self, path: str, **changes: str | int | None) -> str:
        query = {
            k: v for k, v in self.model_copy(update=changes).model_dump().items() if v
        }
        return path + (f"?{urlencode(query)}" if query else "")


class FeedPage(BaseModel):
    feed: list[FeedItem]
    next_offset: int | None


class WatchRows(BaseModel):
    board: Leaderboard
    groups: list[str]


class PageView(FeedPage, WatchRows):
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
    return [("All", filters.url("/reels", group=None), not filters.group)] + [
        (g, filters.url("/reels", group=g), g == filters.group)
        for g in list_groups(owner)
    ]


def _short(views: int) -> str:
    return f"{views // _MILLION}M" if views >= _MILLION else f"{views // 1000}k"


def _rank_links(filters: Filters) -> list[tuple[str, str | None, str, bool]]:
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
    return _feed_page(_filters(group, rank, ratio), offset, scope)


def cover(code: str, scope: int) -> Path:
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
    group = GroupForm(group=name).group
    if not group:
        msg = "Name the group"
        raise RefusedError(msg)
    create_group(group, owner_id)
    return group


def add(profiles: str, group: str, owner_id: int) -> WatchAdded:
    form = WatchForm(profiles=profiles, group=group)
    try:
        usernames = form.usernames
    except ValueError as exc:
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
    handle = _handle(username)
    target = GroupForm(group=group).group
    if not set_group(handle, target or None, owner_id):
        msg = f"@{handle} is not watched"
        raise NotFoundError(msg)
    return ProfileMoved(username=handle, group=target)


def remove(username: str, owner_id: int) -> str:
    handle = _handle(username)
    if not unwatch(handle, owner_id):
        msg = f"@{handle} is not watched"
        raise NotFoundError(msg)
    return handle


class PickedFile(BaseModel):
    path: Path
    media_type: str
    folder: Path

    def forget(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)


def _download(client: InstagramClient, reel: ReelDownload, job_dir: Path) -> Path:
    return client.download_video(str(reel.video_url), job_dir / f"{reel.code}.mp4")


def _picked(reels: str, scope: int) -> list[ReelDownload]:
    try:
        picked = _DOWNLOADS.validate_json(reels)
    except ValidationError as exc:
        msg = "Invalid reel selection"
        raise RefusedError(msg) from exc
    max_videos = settings.web.clone_max_videos
    if not 1 <= len(picked) <= max_videos:
        msg = f"Pick between 1 and {max_videos} reels"
        raise RefusedError(msg)
    own = {item.reel.code for item in FeedItem.of(list_watched(None, scope))}
    if any(reel.code not in own for reel in picked):
        msg = "No such reel"
        raise NotFoundError(msg)
    return picked


def _download_picked(reels: str, scope: int) -> list[Path]:
    picked = _picked(reels, scope)
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
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


def picked_file(reels: str, scope: int) -> PickedFile:
    files = _download_picked(reels, scope)
    job_dir = files[0].parent
    if len(files) == 1:
        return PickedFile(path=files[0], media_type="video/mp4", folder=job_dir)
    archive = job_dir / "reels.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        for file in files:
            zf.write(file, file.name)
    return PickedFile(path=archive, media_type="application/zip", folder=job_dir)


def clone(reels: str, scope: int, assets_root: Path) -> Redirect:
    files = _download_picked(reels, scope)
    try:
        paths = [
            str(path)
            for file in files
            if (path := register_generated_asset(file, assets_root)) is not None
        ]
    finally:
        shutil.rmtree(files[0].parent, ignore_errors=True)
    return Redirect(redirect="/clone?" + urlencode([("video", p) for p in paths]))
