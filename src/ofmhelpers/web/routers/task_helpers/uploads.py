"""Where user files land, and what is allowed to land there.

The shared asset store is content-addressed (sha256), so the same file
uploaded twice -- under a different name, through a different tool's form --
is only ever stored once.
"""

import hashlib
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


# Everything the app writes on behalf of a user lives under this one root, one
# subdir per tool -- it is bind-mounted, so a container swap keeps it. Every
# router derives its own dir from here rather than repeating the literal.
UPLOADS_ROOT = Path(settings.infra.uploads_root)


# Single shared store for reference-asset uploads (Seedance/Kling/Nano Banana
# Pro reference images/videos/audio) -- content-addressed so the same file
# uploaded twice (even under a different name, even through a different
# tool's form) is only ever stored once. See save_asset().
ASSETS_ROOT = UPLOADS_ROOT / "assets"


# Keep in step with utils/metadata_cleaner.SUPPORTED_EXTENSIONS: anything the
# cleaner accepts must classify as an image here, or require_upload_kind
# rejects an upload the tool behind it can actually process.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}


VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}


AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg"}


def classify_kind(name: str) -> str:
    """image / video / audio / other, by file extension."""
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


IMAGE_KINDS = frozenset({"image"})


IMAGE_VIDEO_KINDS = frozenset({"image", "video"})


MEDIA_KINDS = frozenset({"image", "video", "audio"})


def require_upload_kind(name: str | None, allowed: frozenset[str]) -> str:
    """safe_filename() plus an extension allowlist. Returns the safe name.

    Extension, not the declared Content-Type: the client controls both, but
    the extension is what later decides how the file is served back (see
    media_response), so that is the thing worth constraining. Keeps
    executable-in-a-browser uploads (.html, .svg, .xhtml) out of the store
    entirely rather than relying on the serving side alone.
    """
    safe = safe_filename(name)
    if classify_kind(safe) not in allowed:
        allowed_exts = sorted(
            ext
            for kind, exts in (
                ("image", IMAGE_EXTS),
                ("video", VIDEO_EXTS),
                ("audio", AUDIO_EXTS),
            )
            if kind in allowed
            for ext in exts
        )
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(allowed_exts)}",
        )
    return safe


def strip_asset_hash_prefix(filename: str) -> str:
    """Strip the "{sha256}__" content-hash prefix save_asset() stores files
    under -- the hash is only there to dedupe/avoid collisions on disk;
    anywhere a file gets shown to a human should show the original name."""
    _, _, rest = filename.partition("__")
    return rest or filename


def safe_filename(name: str | None) -> str:
    """The basename of a client-supplied upload name, and nothing else.

    `UploadFile.filename` is whatever the client put in the multipart part --
    "../../cookies/cookies.txt" is a legal value, and joining that onto an
    upload directory writes outside it. Every path built from an upload name
    goes through here first. Backslashes are folded to "/" so a Windows-style
    path is stripped the same way on Linux, where "\\" is an ordinary
    filename character.
    """
    base = Path((name or "").replace("\\", "/")).name
    if not base or base in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid file name")
    return base


def make_job_dir(upload_root: Path) -> Path:
    """A fresh, unique directory for one job's uploads to live in."""
    job_dir = upload_root / uuid.uuid4().hex[:8]
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def save_upload(job_dir: Path, upload: UploadFile) -> str:
    dest = job_dir / safe_filename(upload.filename)
    with dest.open("wb") as out:
        shutil.copyfileobj(upload.file, out)
    return str(dest)


def save_asset(upload: UploadFile, assets_root: Path | None = None) -> str:
    """Save a reference-asset upload into the shared, content-addressed
    store, deduping by content hash. Streams + hashes in chunks (these can be
    videos) rather than reading the whole upload into memory.

    The stored filename is "{sha256}__{original name}" -- the hash prefix
    guarantees no collisions and makes "does this already exist" an O(1)
    glob instead of hashing every file already on disk; the original name is
    kept after the prefix purely for display (see refs.py)."""
    # Before the temp file, so a rejected name doesn't leave one behind.
    # Audio is allowed here (unlike the todo/model uploads): this store backs
    # the reference-audio inputs of the generation tools.
    stored_name = require_upload_kind(upload.filename, MEDIA_KINDS)
    # Resolved here, not as a parameter default: a default binds at import,
    # which would pin the store to whatever OFM_UPLOADS_ROOT said then.
    assets_root = assets_root or ASSETS_ROOT
    assets_root.mkdir(parents=True, exist_ok=True)

    hasher = hashlib.sha256()
    with tempfile.NamedTemporaryFile(dir=assets_root, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        for chunk in iter(lambda: upload.file.read(1 << 20), b""):
            hasher.update(chunk)
            tmp.write(chunk)
    digest = hasher.hexdigest()

    existing = next(assets_root.glob(f"{digest}__*"), None)
    if existing is not None:
        tmp_path.unlink()
        return str(existing)

    final_path = assets_root / f"{digest}__{stored_name}"
    try:
        tmp_path.rename(final_path)
    except FileExistsError:
        # Lost a race with a concurrent upload of the same content -- fine,
        # that copy is just as good.
        tmp_path.unlink()
    return str(final_path)


def register_generated_asset(
    path: Path, assets_root: Path | None = None
) -> Path | None:
    """Link a freshly-generated output file into the shared, content-addressed
    asset store so it shows up in the "reuse an uploaded ..." picker right
    away, exactly like a manual upload -- generated output used to live only
    in OUT_DIR (kieai_out/), a tree /refs never looks at. Same naming scheme
    as save_asset() (content-hash prefix), so a generated file that happens
    to match an already-uploaded one dedupes for free.

    Hardlinks rather than copying (falls back to a copy across filesystems)
    -- cheap, and the link's mtime matches the source's, so a fresh
    generation naturally sorts to the top of /refs alongside real uploads.

    Best-effort: the job already finished generating by the time this runs,
    so a bookkeeping failure here (e.g. a test double pointing at a path that
    was never really written) must never turn a successful job into a failed
    one -- just skip registering it."""
    assets_root = assets_root or ASSETS_ROOT
    try:
        assets_root.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()

        existing = next(assets_root.glob(f"{digest}__*"), None)
        if existing is not None:
            return existing

        dest = assets_root / f"{digest}__{path.name}"
        try:
            os.link(path, dest)
        except OSError:
            # cross-device or a filesystem without hardlinks -- copy instead.
            # A failure here still falls through to the outer handler.
            shutil.copy2(path, dest)
    except OSError:
        logger.warning("could not register generated asset %s", path, exc_info=True)
        return None
    else:
        return dest


def register_grouped_results(results: list[dict]) -> None:
    """Adopt every output file of a grouped download job into the shared asset
    store, so downloaded reels/images show up in the "reuse an uploaded ..."
    picker instead of forcing a download-then-reupload round trip.

    Mutates nothing the caller depends on: register_generated_asset() dedupes
    by content hash and is already best-effort, so a file that fails to link
    just doesn't appear in the picker.

    Reads ASSETS_ROOT at call time rather than taking it as a defaulted
    argument, so the destination stays overridable per-test."""
    for result in results:
        for raw in result.get("output_paths", []):
            register_generated_asset(Path(raw), ASSETS_ROOT)
