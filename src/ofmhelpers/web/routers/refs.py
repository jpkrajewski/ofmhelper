import heapq
import os
import subprocess
import threading
from functools import lru_cache
from operator import itemgetter
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from PIL import Image

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web import ref_usage
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.previews import (
    write_atomic,
    write_image_thumb,
    write_video_thumb,
)
from ofmhelpers.web.routers.task_helpers import (
    ASSETS_ROOT,
    assets_root_for,
    classify_kind,
    in_asset_root,
    media_response,
    strip_asset_hash_prefix,
)
from ofmhelpers.web.schemas import RefEntry

logger = get_logger(__name__)

router = APIRouter(prefix="/refs", tags=["refs"])

# Reuse-picker/inputs previews only ever need a small box (see .ref-tile /
# .thumb-small CSS) -- serving the full original (multi-MB) just to paint
# ~100px is what made the picker slow to open. Cached on disk keyed by the
# same content hash already in the filename, so repeat views (or the same
# file shown in multiple pickers) never re-decode the original.
THUMBS_DIR = ASSETS_ROOT / ".thumbs"


# What the reuse picker opens on: the handful you actually just worked with,
# then the handful you most recently added. Two short lists beat one long one
# -- the file you want is nearly always in one of them. The picker's "show
# older" button asks for an explicit ?limit= instead.
RECENT_USED_LIMIT = settings.web.recent_used_limit
RECENT_UPLOAD_LIMIT = settings.web.recent_upload_limit
MAX_REF_LIMIT = settings.web.max_ref_limit


@lru_cache(maxsize=1024)
def _making(thumb: Path) -> threading.Lock:  # noqa: ARG001 -- the cache key
    """One lock per thumbnail being made: a burst of requests for the same
    new file (Recreate clicked over and over) decodes the original once,
    the rest wait for that file instead of each decoding it again."""
    return threading.Lock()


def _entry(path: str, mtime: float, used_at: float | None = None) -> RefEntry:
    name = Path(path).name
    return RefEntry(
        path=path,
        name=strip_asset_hash_prefix(name),
        kind=classify_kind(name),
        mtime=mtime,
        used_at=used_at,
    )


def _stored_files(root: Path, kind: str | None) -> dict[str, float]:
    """Every file under `root` (the session's part of the asset store,
    `in_asset_root`), optionally of one kind, minus the thumbnail cache, as
    path -> mtime. No separate metadata store, this is the real files: one
    walk, one stat per file; the thumbnail cache (one file per source per
    size) and other people's folders are never descended into."""
    found: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if Path(dirpath, d) != THUMBS_DIR
            and in_asset_root(root, Path(dirpath, d, "_"))
        ]
        for name in filenames:
            if kind and classify_kind(name) != kind:
                continue
            path = Path(dirpath, name)
            try:
                found[str(path)] = path.stat().st_mtime
            except OSError:
                continue  # deleted mid-walk, or a dangling link
    return found


def _require_visible(request: Request, path: str) -> Path:
    """A client-supplied path, refused unless it is a file inside the
    session's part of the asset store -- nobody, admins included, fetches
    another person's file by guessing its path."""
    file_path = Path(path)
    if not in_asset_root(assets_root_for(request), file_path):
        raise HTTPException(status_code=403, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return file_path


@router.get("")
def list_refs(
    request: Request,
    kind: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=MAX_REF_LIMIT)] = None,
):
    """The reuse picker's list.

    Default: the `RECENT_USED_LIMIT` files you (this user) last *picked* (web/ref_usage.py,
    written by task_helpers.build_ordered_paths), then the
    `RECENT_UPLOAD_LIMIT` most recently *uploaded* ones that aren't already in
    that list. Those are two different orderings on purpose -- mtime means
    "uploaded" and nothing rewrites it, since save_asset is content-deduped and
    resolving a reused path no longer touches the file.

    An explicit `?limit=` is the "show older" path: newest-uploaded first, no
    usage grouping."""
    stored = _stored_files(assets_root_for(request), kind)
    if limit is not None:
        newest = heapq.nlargest(limit, stored.items(), key=itemgetter(1))
        return [_entry(path, mtime) for path, mtime in newest]

    used: list[RefEntry] = []
    for raw, used_at in ref_usage.recent(
        AuthMiddleware.owner_id(request), MAX_REF_LIMIT
    ):
        # A used file can have been deleted through the file manager since,
        # or be of another kind than this picker wants.
        mtime = stored.pop(raw, None)
        if mtime is not None:
            used.append(_entry(raw, mtime, used_at))
        if len(used) == RECENT_USED_LIMIT:
            break

    uploaded = heapq.nlargest(RECENT_UPLOAD_LIMIT, stored.items(), key=itemgetter(1))
    return used + [_entry(path, mtime) for path, mtime in uploaded]


@router.get("/file")
def get_ref_file(request: Request, path: Annotated[str, Query()]):
    file_path = _require_visible(request, path)
    # Display name (hash prefix stripped) decides the type, and non-media is
    # forced to a download -- see media_response.
    return media_response(file_path, strip_asset_hash_prefix(file_path.name))


def _make_thumb(source: Path, thumb: Path, kind: str, size: int) -> None:
    """Writes the thumbnail unless a request it waited for just did."""
    if thumb.is_file():
        return
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "image":
            write_atomic(thumb, lambda tmp: write_image_thumb(source, tmp, size))
        else:
            write_video_thumb(source, thumb, size)
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        Image.UnidentifiedImageError,
    ):
        # A corrupt/unreadable source shouldn't 500 the whole picker grid
        # -- the client falls back to its icon tile on a 404.
        logger.warning("could not thumbnail %s", source, exc_info=True)
        raise HTTPException(
            status_code=404, detail="Could not generate thumbnail"
        ) from None


@router.get("/thumb")
def get_ref_thumb(
    request: Request,
    path: Annotated[str, Query()],
    size: Annotated[int, Query(ge=16, le=1024)] = 200,
):
    """Small cached preview for the reuse-picker/inputs grids -- never the
    original (see THUMBS_DIR comment). Images thumbnail through Pillow, videos
    through an ffmpeg first-frame grab; audio has no visual frame to show and
    still gets an icon client-side."""
    file_path = _require_visible(request, path)

    kind = classify_kind(file_path.name)
    if kind not in ("image", "video"):
        raise HTTPException(status_code=404, detail="No thumbnail for this file type")

    # Videos share the cache namespace with images but not the key -- the
    # digest is the file's own content hash, so there's no collision.
    digest = file_path.name.split("__", 1)[0]
    thumb_path = THUMBS_DIR / f"{digest}__{size}.webp"
    if not thumb_path.is_file():
        with _making(thumb_path):
            _make_thumb(file_path, thumb_path, kind, size)

    return FileResponse(
        thumb_path,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
