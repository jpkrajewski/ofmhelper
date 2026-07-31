import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from PIL import Image

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web import ref_usage
from ofmhelpers.web.routers.task_helpers import (
    ASSETS_ROOT,
    classify_kind,
    media_response,
    strip_asset_hash_prefix,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/refs", tags=["refs"])

# A first frame is enough to recognise a clip in a ~100-200px tile, and
# seeking costs nothing at position 0.
_FFMPEG_TIMEOUT_S = settings.web.ffmpeg_timeout_s

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


def _entry(path: Path, used_at: float | None = None) -> dict:
    return {
        "path": str(path),
        "name": strip_asset_hash_prefix(path.name),
        "kind": classify_kind(path.name),
        "mtime": path.stat().st_mtime,
        # None = "never picked, just uploaded". The picker groups on this.
        "used_at": used_at,
    }


def _stored_files(kind: str | None) -> list[Path]:
    """Every file in the shared asset store, optionally of one kind -- no
    separate metadata store, this is the real files."""
    return [
        path
        for path in ASSETS_ROOT.glob("*")
        if path.is_file() and (not kind or classify_kind(path.name) == kind)
    ]


@router.get("")
def list_refs(
    kind: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=MAX_REF_LIMIT)] = None,
):
    """The reuse picker's list.

    Default: the `RECENT_USED_LIMIT` files you last *picked* (web/ref_usage.py,
    written by task_helpers.build_ordered_paths), then the
    `RECENT_UPLOAD_LIMIT` most recently *uploaded* ones that aren't already in
    that list. Those are two different orderings on purpose -- mtime means
    "uploaded" and nothing rewrites it, since save_asset is content-deduped and
    resolving a reused path no longer touches the file.

    An explicit `?limit=` is the "show older" path: newest-uploaded first, no
    usage grouping."""
    if limit is not None:
        newest = sorted(
            _stored_files(kind), key=lambda p: p.stat().st_mtime, reverse=True
        )
        return [_entry(p) for p in newest[:limit]]

    stored = {str(p): p for p in _stored_files(kind)}
    used: list[dict] = []
    for raw, used_at in ref_usage.recent(MAX_REF_LIMIT):
        # A used file can have been deleted through the file manager since,
        # or be of another kind than this picker wants.
        path = stored.pop(raw, None)
        if path is not None:
            used.append(_entry(path, used_at))
        if len(used) == RECENT_USED_LIMIT:
            break

    uploaded = sorted(stored.values(), key=lambda p: p.stat().st_mtime, reverse=True)
    return used + [_entry(p) for p in uploaded[:RECENT_UPLOAD_LIMIT]]


@router.get("/file")
def get_ref_file(path: Annotated[str, Query()]):
    file_path = Path(path)
    # keep this scoped to the shared asset store
    if ASSETS_ROOT.resolve() not in file_path.resolve().parents:
        raise HTTPException(status_code=403, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    # Display name (hash prefix stripped) decides the type, and non-media is
    # forced to a download -- see media_response.
    return media_response(file_path, strip_asset_hash_prefix(file_path.name))


def write_image_thumb(source: Path, dest: Path, size: int) -> None:
    with Image.open(source) as img:
        img.thumbnail((size, size))
        img.convert("RGB").save(dest, "WEBP")


def _write_video_thumb(source: Path, dest: Path, size: int) -> None:
    """First frame, scaled to fit a `size` box, as webp.

    Extracted with ffmpeg rather than served as a <video preload="metadata">
    tile: the old client-side approach made the browser range-request every
    clip in the grid just to paint a poster frame, which only got worse as
    downloaded reels started landing in the asset store. A cached frame is a
    few KB instead.

    `scale` keeps the aspect ratio (-1) and `force_original_aspect_ratio`
    guards the portrait case, so a 9:16 reel isn't stretched.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        msg = "ffmpeg not available"
        raise FileNotFoundError(msg)

    # Write via a temp file in the same dir: a killed/timed-out ffmpeg must
    # not leave a truncated .webp behind that every later request then treats
    # as a valid cache hit.
    with tempfile.NamedTemporaryFile(
        dir=dest.parent, suffix=".webp", delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                f"scale={size}:{size}:force_original_aspect_ratio=decrease",
                "-f",
                "webp",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
            timeout=_FFMPEG_TIMEOUT_S,
        )
        tmp_path.replace(dest)
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/thumb")
def get_ref_thumb(
    path: Annotated[str, Query()], size: Annotated[int, Query(ge=16, le=1024)] = 200
):
    """Small cached preview for the reuse-picker/inputs grids -- never the
    original (see THUMBS_DIR comment). Images thumbnail through Pillow, videos
    through an ffmpeg first-frame grab; audio has no visual frame to show and
    still gets an icon client-side."""
    file_path = Path(path)
    if ASSETS_ROOT.resolve() not in file_path.resolve().parents:
        raise HTTPException(status_code=403, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    kind = classify_kind(file_path.name)
    if kind not in ("image", "video"):
        raise HTTPException(status_code=404, detail="No thumbnail for this file type")

    # Videos share the cache namespace with images but not the key -- the
    # digest is the file's own content hash, so there's no collision.
    digest = file_path.name.split("__", 1)[0]
    thumb_path = THUMBS_DIR / f"{digest}__{size}.webp"
    if not thumb_path.is_file():
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if kind == "image":
                write_image_thumb(file_path, thumb_path, size)
            else:
                _write_video_thumb(file_path, thumb_path, size)
        except (
            OSError,
            ValueError,
            subprocess.SubprocessError,
            Image.UnidentifiedImageError,
        ):
            # A corrupt/unreadable source shouldn't 500 the whole picker grid
            # -- the client falls back to its icon tile on a 404.
            logger.warning("could not thumbnail %s", file_path, exc_info=True)
            raise HTTPException(
                status_code=404, detail="Could not generate thumbnail"
            ) from None

    return FileResponse(
        thumb_path,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
