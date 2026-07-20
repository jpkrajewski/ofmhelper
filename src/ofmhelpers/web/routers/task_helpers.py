"""
Shared plumbing for every "upload files -> run in background -> download
result" router (clean-images, download-videos, kling3, nanobanana,
seedance, ...).

Each router should only need to define its own processing function and
wire it through these helpers -- everything else (saving uploads,
reusing previously-uploaded refs without duplicating them, validating
paths from the client, serving the final file) lives here once.
"""

import json
import mimetypes
import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile, HTTPException
from fastapi.responses import FileResponse


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
    job_dir: Path,
    manifest_json: str,
    new_files: list[UploadFile],
    allowed_root: Path,
) -> list[str]:
    """Reconstructs an ordered list of file paths from a JSON manifest like
    [{"kind": "new"}, {"kind": "existing", "path": "..."}]. Only genuinely
    new uploads get saved; existing refs are reused by path, never
    re-uploaded -- this is what prevents duplicate files on disk."""
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
            paths.append(save_upload(job_dir, upload))
    return paths


def attach_download_indexes(job: dict) -> None:
    """Call in a job-status route once job['status'] == 'done' so the
    template can link to /files/<job_id>/<index> for each result file."""
    if job.get("status") == "done" and job.get("result"):
        for idx, f in enumerate(job["result"]):
            f["index"] = idx


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

    path = Path(files[index]["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer exists on server")

    if as_attachment:
        return FileResponse(
            path, filename=path.name, media_type="application/octet-stream"
        )

    media_type = mimetypes.guess_type(path.name)[0] or default_media_type
    return FileResponse(path, media_type=media_type)
