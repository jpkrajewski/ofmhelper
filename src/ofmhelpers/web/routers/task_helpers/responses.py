"""The response shapes every status/polling page reuses.

None of these touch disk beyond a stat: they turn a job dict into what a
template or the polling JS expects, so every tool's status endpoint is one
line rather than its own bespoke payload.
"""

from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from ofmhelpers.web.routers.task_helpers.uploads import (
    classify_kind,
    strip_asset_hash_prefix,
)


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

    assets: list[dict] = []
    failed_sources: list[dict] = []
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
