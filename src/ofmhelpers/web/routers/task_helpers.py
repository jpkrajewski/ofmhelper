"""
Shared plumbing for every "upload files -> run in background -> download
result" router (clean-images, download-videos, kling3, nanobanana,
seedance, ...).

Each router should only need to define its own processing function and
wire it through these helpers -- everything else (saving uploads,
reusing previously-uploaded refs without duplicating them, validating
paths from the client, serving the final file) lives here once.
"""

import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse

from ofmhelpers.log import get_logger

logger = get_logger(__name__)
# Single shared store for reference-asset uploads (Seedance/Kling/Nano Banana
# Pro reference images/videos/audio) -- content-addressed so the same file
# uploaded twice (even under a different name, even through a different
# tool's form) is only ever stored once. See save_asset().
ASSETS_ROOT = Path("uploads") / "assets"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
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


def strip_asset_hash_prefix(filename: str) -> str:
    """Strip the "{sha256}__" content-hash prefix save_asset() stores files
    under -- the hash is only there to dedupe/avoid collisions on disk;
    anywhere a file gets shown to a human should show the original name."""
    _, _, rest = filename.partition("__")
    return rest or filename


def make_job_dir(upload_root: Path) -> Path:
    """A fresh, unique directory for one job's uploads to live in."""
    job_dir = upload_root / uuid.uuid4().hex[:8]
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def save_upload(job_dir: Path, upload: UploadFile) -> str:
    dest = job_dir / upload.filename
    with dest.open("wb") as out:
        shutil.copyfileobj(upload.file, out)
    return str(dest)


def save_asset(upload: UploadFile, assets_root: Path = ASSETS_ROOT) -> str:
    """Save a reference-asset upload into the shared, content-addressed
    store, deduping by content hash. Streams + hashes in chunks (these can be
    videos) rather than reading the whole upload into memory.

    The stored filename is "{sha256}__{original name}" -- the hash prefix
    guarantees no collisions and makes "does this already exist" an O(1)
    glob instead of hashing every file already on disk; the original name is
    kept after the prefix purely for display (see refs.py)."""
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

    final_path = assets_root / f"{digest}__{upload.filename}"
    try:
        tmp_path.rename(final_path)
    except FileExistsError:
        # Lost a race with a concurrent upload of the same content -- fine,
        # that copy is just as good.
        tmp_path.unlink()
    return str(final_path)


def register_generated_asset(
    path: Path, assets_root: Path = ASSETS_ROOT
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


def resolve_existing_ref(raw_path: str, allowed_root: Path) -> Path:
    """Validate a path the client claims points at a previously-uploaded
    file. Raises HTTPException(400) if it's outside allowed_root or
    doesn't actually exist -- never trust a client-supplied path as-is."""
    allowed_root = allowed_root.resolve()
    resolved = Path(raw_path).resolve()
    if allowed_root != resolved and allowed_root not in resolved.parents:
        raise HTTPException(status_code=400, detail="Invalid reference path")
    if not resolved.is_file():
        raise HTTPException(
            status_code=400, detail=f"Reference file not found: {raw_path}"
        )
    return resolved


def build_ordered_paths(
    manifest_json: str,
    new_files: list[UploadFile],
    allowed_root: Path,
) -> list[str]:
    """Reconstructs an ordered list of file paths from a JSON manifest like
    [{"kind": "new"}, {"kind": "existing", "path": "..."}]. Genuinely new
    uploads go through save_asset (content-deduped); existing refs are
    resolved by path, never re-uploaded -- together this is what prevents
    duplicate files on disk."""
    try:
        manifest = json.loads(manifest_json)
    except json.JSONDecodeError:
        manifest = [{"kind": "new"} for _ in new_files]

    new_files_iter = iter(new_files)
    paths: list[str] = []
    for entry in manifest:
        if entry.get("kind") == "existing":
            paths.append(str(resolve_existing_ref(entry["path"], allowed_root)))
        else:
            upload = next(new_files_iter, None)
            if upload is None or not upload.filename:
                continue
            paths.append(save_asset(upload, allowed_root))
    return paths


def asset_card(
    name: str,
    index: int,
    files_prefix: str,
    source: str | None = None,
    remote_url: str | None = None,
) -> dict:
    """One entry for the generic asset grid / status page: what kind of
    preview to render, and the two URLs a client can already reach (never
    the server-side path a result dict carries internally).

    remote_url, when set, is always preferred (kie.ai is faster than
    proxying through our own server, and it keeps the file for 14 days) --
    but kie.ai's URL is only reliably valid ~24h, so `local_fallback_url` is
    always included too (even when remote_url is what's actually used) so
    the frontend can swap to our own copy client-side (an <video onerror>,
    see generation.js) once the hosted one goes stale. There may be no local
    copy at all (see job_status_payload's remote-only result) -- callers
    that never had a local file just won't see this field used."""
    local_url = f"{files_prefix}/{index}"
    return {
        "name": name,
        "index": index,
        "kind": classify_kind(name),
        "view_url": remote_url or local_url,
        "download_url": remote_url or f"{local_url}?dl=1",
        "local_fallback_url": local_url if remote_url else None,
        "source": source,
    }


def flatten_grouped_results(job: dict, files_prefix: str) -> tuple[list, list]:
    """download-videos / download-images store results grouped per source URL
    ([{url, success, output_paths | error}, ...]). Flattens that into the
    generic asset-card list (source URL riding along) plus a list of failed
    sources. The download index must count across ALL groups -- it has to
    line up with what the /files/{job_id}/{index} route's own flattening
    produces."""
    assets = []
    failed_sources = []
    idx = 0
    for r in job.get("result") or []:
        if not r["success"]:
            failed_sources.append({"source": r["url"], "error": r["error"]})
            continue
        for p in r["output_paths"]:
            assets.append(
                asset_card(Path(p).name, idx, f"{files_prefix}/{job['id']}", r["url"])
            )
            idx += 1
    return assets, failed_sources


def grouped_job_status_payload(job: dict | None, files_prefix: str) -> dict:
    """job_status_payload's sibling for the grouped-by-URL download tasks:
    same shape, plus failed_sources so the UI can report partially-failed
    runs (job "done" but some URLs errored)."""
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    assets, failed_sources = [], []
    if job.get("status") == "done":
        assets, failed_sources = flatten_grouped_results(job, files_prefix)

    return {
        "job_id": job["id"],
        "task": job["task"],
        "params": job["params"],
        "status": job["status"],
        "error": job.get("error"),
        "result": assets,
        "failed_sources": failed_sources,
    }


def reference_asset(path: str) -> dict:
    """One reference-file entry for a job's "inputs" display: a file already
    sitting in the shared uploads/assets/ store. Only ever rendered small
    (see .inputs-refs .result-item CSS, ~200px), so images go through
    /refs/thumb (cached, small) rather than /refs/file (full original) --
    this dict has no other consumer that needs the full-res URL."""
    name = strip_asset_hash_prefix(Path(path).name)
    kind = classify_kind(name)
    quoted = quote(path)
    view_url = (
        f"/refs/thumb?path={quoted}&size=200"
        if kind == "image"
        else f"/refs/file?path={quoted}"
    )
    return {
        "name": name,
        "kind": kind,
        "view_url": view_url,
    }


# Params long enough that they belong in their own block (below the settings
# table) rather than crammed into a table cell -- currently just the prompt,
# but keyed generically in case a future field needs the same treatment.
_LONG_TEXT_THRESHOLD = 60


def job_inputs(job: dict) -> dict:
    """Splits a job's stored params into the three shapes the "Inputs"
    section on an AI-generation job-status page renders: short scalar
    settings (a table), long text (prompt -- its own block), and reference
    file lists (previewed via reference_asset). Generic over every AI-gen
    task's params shape -- a param is a reference-file list simply because
    its value is a list at all; none of these jobs ever store a list of
    anything else."""
    settings, long_text, file_groups = [], [], []
    for key, value in (job.get("params") or {}).items():
        label = key.replace("_", " ")
        if isinstance(value, list):
            if value:  # an empty ref slot has nothing worth showing
                file_groups.append(
                    {"label": label, "assets": [reference_asset(p) for p in value]}
                )
        elif isinstance(value, str) and len(value) > _LONG_TEXT_THRESHOLD:
            long_text.append({"label": label, "value": value})
        else:
            settings.append({"label": label, "value": value})
    return {"settings": settings, "long_text": long_text, "file_groups": file_groups}


def job_status_payload(job: dict | None, files_prefix: str) -> dict:
    """JSON body for the `/{prefix}/jobs/{job_id}/status` polling endpoint used
    by the inline (no-redirect) generation UI, and by the /generate gallery's
    click-to-reuse feature (task/params)."""
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    result = []
    if job.get("status") == "done":
        for idx, f in enumerate(job.get("result") or []):
            result.append(
                asset_card(
                    f["name"],
                    idx,
                    f"{files_prefix}/{job['id']}",
                    remote_url=f.get("remote_url"),
                )
            )

    payload = {
        "job_id": job["id"],
        "task": job["task"],
        "params": job["params"],
        "status": job["status"],
        "error": job.get("error"),
        "result": result,
    }
    # A generation still in flight can already have a hosted result URL
    # (kie.ai's poll succeeded; only the local download is still running) --
    # surface it so the frontend can render it immediately instead of
    # blocking on the download. Cleared implicitly once status flips to
    # "done"/"failed" (this key is only ever read while still "running").
    if job.get("status") == "running" and job.get("preview"):
        payload["preview"] = job["preview"]
    return payload


def serve_job_file(
    job: dict | None,
    index: int,
    as_attachment: bool = True,
    default_media_type: str = "application/octet-stream",
) -> FileResponse:
    """Generic '/files/{job_id}/{index}' implementation. The URL only ever
    carries a job id + integer index -- never a raw path -- and this only
    ever serves a file that job's own result already points to."""
    if job is None or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Job not found or not finished")

    files = job.get("result") or []
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="File not found")

    raw_path = files[index].get("path")
    if raw_path is None:
        # Local download never completed -- this asset only ever existed as
        # a remote_url (see job_status_payload), which the client already
        # has and points its view/download links at directly. There's
        # nothing local for this route to serve.
        raise HTTPException(status_code=404, detail="File was never downloaded locally")

    path = Path(raw_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer exists on server")

    if as_attachment:
        return FileResponse(
            path, filename=path.name, media_type="application/octet-stream"
        )

    media_type = mimetypes.guess_type(path.name)[0] or default_media_type
    return FileResponse(path, media_type=media_type)
