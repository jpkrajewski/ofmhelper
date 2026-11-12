"""The reuse picker's side of the asset store (`/refs`): the files of a
person's part of the store as they are on disk -- no separate metadata store
--, which of them they may fetch, and their small cached thumbnails."""

import heapq
import os
import re
import threading
from functools import lru_cache
from operator import attrgetter
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.stores import downloads
from ofmhelpers.features.assets import ref_usage, store
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.store import in_asset_root, strip_asset_hash_prefix
from ofmhelpers.features.edit.service import STAGING as EDIT_STAGING
from ofmhelpers.features.previews.render import (
    PREVIEW_ERRORS,
    write_atomic,
    write_image_thumb,
    write_video_thumb,
)
from ofmhelpers.features.refusals import ForbiddenError, NotFoundError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import MissingRefs, RefEntry, StoredRef, StoredRefs
from ofmhelpers.schemas.files import RestorableRef

logger = get_logger(__name__)


class StoredFile(BaseModel):
    """A file in the store; `mtime` = when it was uploaded (nothing rewrites
    it: `save_asset` is content-deduped)."""

    path: str
    mtime: float


# Reuse-picker/inputs previews only ever need a small box (see .ref-tile /
# .thumb-small CSS) -- serving the full original (multi-MB) just to paint
# ~100px is what made the picker slow to open. Cached on disk keyed by the
# same content hash already in the filename, so repeat views (or the same
# file shown in multiple pickers) never re-decode the original.
def thumbs_dir() -> Path:
    return store.assets_root() / ".thumbs"


@lru_cache(maxsize=1024)
def _making(thumb: Path) -> threading.Lock:  # noqa: ARG001 -- the cache key
    """One lock per thumbnail being made: a burst of requests for the same
    new file (Recreate clicked over and over) decodes the original once,
    the rest wait for that file instead of each decoding it again."""
    return threading.Lock()


def _entry(file: StoredFile, used_at: float | None = None) -> RefEntry:
    name = Path(file.path).name
    return RefEntry(
        path=file.path,
        name=strip_asset_hash_prefix(name),
        kind=classify_kind(name),
        mtime=file.mtime,
        used_at=used_at,
    )


def stored_files(root: Path, kind: str | None) -> list[StoredFile]:
    """Every file under `root` (a person's part of the asset store,
    `in_asset_root`), optionally of one kind, minus the thumbnail cache and
    /edit's `.edit/` (kept a day, never offered for reuse). One walk, one stat per file; the thumbnail cache (one
    file per source per size) and other people's folders are never descended
    into."""
    found: list[StoredFile] = []
    thumbs = thumbs_dir()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if Path(dirpath, d) != thumbs
            and d != EDIT_STAGING
            and in_asset_root(root, Path(dirpath, d, "_"))
        ]
        for name in filenames:
            if kind and classify_kind(name) != kind:
                continue
            path = Path(dirpath, name)
            try:
                found.append(StoredFile(path=str(path), mtime=path.stat().st_mtime))
            except OSError:
                continue  # deleted mid-walk, or a dangling link
    return found


def _newest(files: list[StoredFile], limit: int) -> list[RefEntry]:
    return [_entry(f) for f in heapq.nlargest(limit, files, key=attrgetter("mtime"))]


def listing(
    assets_root: Path, owner_id: int, kind: str | None, limit: int | None
) -> list[RefEntry]:
    """The reuse picker's list.

    Default: the handful (`recent_used_limit`) of files this person last
    *picked* (`ref_usage`, written by `manifests.build_ordered_paths`), then
    the handful (`recent_upload_limit`) most recently *uploaded* ones that
    aren't already in that list: two short lists beat one long one, the file
    you want is nearly always in one of them. Those are two different
    orderings on purpose -- mtime means "uploaded" and nothing rewrites it.

    A `limit` is the "show older" path: newest-uploaded first, no usage
    grouping."""
    files = stored_files(assets_root, kind)
    if limit is not None:
        return _newest(files, limit)
    stored = {f.path: f for f in files}
    used: list[RefEntry] = []
    web = settings.web
    for ref in ref_usage.recent(owner_id, web.max_ref_limit):
        # A used file can have been deleted through the file manager since,
        # or be of another kind than this picker wants.
        file = stored.pop(ref.path, None)
        if file is not None:
            used.append(_entry(file, ref.used_at))
        if len(used) == web.recent_used_limit:
            break
    return used + _newest(list(stored.values()), web.recent_upload_limit)


_DIGEST = re.compile(r"[0-9a-f]{64}")


def _restorable(path: str, actor: str | None) -> RestorableRef | None:
    """`path` as `actor`'s own download the retention sweep removed
    (`downloads.asset_path`, matched by the content digest in its name)."""
    digest = Path(path).name.split("__", 1)[0]
    if actor is None or not actor.isdecimal() or not _DIGEST.fullmatch(digest):
        return None
    found = downloads.by_asset(int(actor), digest)
    if not found:
        return None
    return RestorableRef(path=path, job_id=found[0].job_id, index=found[0].position)


def missing(
    assets_root: Path, paths: list[str], actor: str | None = None
) -> MissingRefs:
    """Of `paths` (a past run's files, restored by Recreate), the ones the
    asker can no longer use: deleted since, never on this server, or not
    their own. `restorable`: those of them that were `actor`'s downloads,
    removed to save space -- the page fetches them again."""
    gone = [
        p
        for p in paths
        if not (in_asset_root(assets_root, Path(p)) and Path(p).is_file())
    ]
    found = (_restorable(path, actor) for path in gone)
    return MissingRefs(
        missing=gone, restorable=[ref for ref in found if ref is not None]
    )


def _stored(assets_root: Path, digest: str) -> StoredRef | None:
    path = next(assets_root.glob(f"{digest}__*"), None)
    if path is None or not path.is_file():
        return None
    return StoredRef(digest=digest, path=str(path))


def stored(assets_root: Path, digests: list[str]) -> StoredRefs:
    """Of `digests` (the sha256 of files just picked from a computer), the
    ones already in the asker's part of the store, where `save_asset` names
    every file by that digest: those need no upload."""
    found = (_stored(assets_root, digest) for digest in digests)
    return StoredRefs(stored=[ref for ref in found if ref is not None])


def visible(assets_root: Path, path: str) -> Path:
    """A client-supplied path, refused unless it is a file inside the
    asker's part of the asset store -- nobody, admins included, fetches
    another person's file by guessing its path."""
    file_path = Path(path)
    if not in_asset_root(assets_root, file_path):
        msg = "Invalid path"
        raise ForbiddenError(msg)
    if not file_path.is_file():
        msg = "File not found"
        raise NotFoundError(msg)
    return file_path


def _make_thumb(source: Path, thumb: Path, kind: str, size: int) -> None:
    """Writes the thumbnail unless a request it waited for just did."""
    if thumb.is_file():
        return
    thumb.parent.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "image":
            write_atomic(thumb, lambda tmp: write_image_thumb(source, tmp, size))
        else:
            write_video_thumb(source, thumb, size)
    except PREVIEW_ERRORS:
        # A corrupt/unreadable source shouldn't 500 the whole picker grid
        # -- the client falls back to its icon tile on a 404.
        logger.warning("could not thumbnail %s", source, exc_info=True)
        msg = "Could not generate thumbnail"
        raise NotFoundError(msg) from None


def thumb(assets_root: Path, path: str, size: int) -> Path:
    """Small cached preview for the reuse-picker/inputs grids -- never the
    original (see the comment above `thumbs_dir`). Images thumbnail through
    Pillow, videos through an ffmpeg first-frame grab; audio has no visual
    frame to show and still gets an icon client-side."""
    file_path = visible(assets_root, path)
    kind = classify_kind(file_path.name)
    if kind not in ("image", "video"):
        msg = "No thumbnail for this file type"
        raise NotFoundError(msg)
    # Videos share the cache namespace with images but not the key -- the
    # digest is the file's own content hash, so there's no collision.
    digest = file_path.name.split("__", 1)[0]
    thumb_path = thumbs_dir() / f"{digest}__{size}.webp"
    if not thumb_path.is_file():
        with _making(thumb_path):
            _make_thumb(file_path, thumb_path, kind, size)
    return thumb_path
