"""A job's result files as the cards a page shows.

Nothing here touches disk beyond a stat.
"""

from pydantic import BaseModel

from ofmhelpers.db.stores import downloads
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.names import download_name
from ofmhelpers.features.previews import render as previews
from ofmhelpers.schemas import AssetCard, FailedSource, Job


class JobAssets(BaseModel):
    """A job's result as cards, and the sources that gave none."""

    assets: list[AssetCard] = []
    failed_sources: list[FailedSource] = []


def asset_card(
    name: str,
    index: int,
    files_prefix: str,
    source: str | None = None,
    remote_url: str | None = None,
    local: bool = True,
    job_id: str = "",
    removed: bool = False,
) -> AssetCard:
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
    that never had a local file just won't see this field used.

    An image or video with a local copy also gets `preview_url` (what a
    gallery shows) and a video `poster_url` (shown until played); see
    features/previews/render.py. A video's is its own URL until the preview
    worker made the small copy: one URL never serves two files (a player that
    held the original's length asked `/preview` for ranges the small copy has
    not got: 4,637 answers 416 in 48 h, prod 2026-11-07).

    `removed`: a download whose video the retention sweep removed -- only
    what was kept of it has a URL (a video's poster, an image's preview)."""
    local_url = f"{files_prefix}/{index}"
    kind = classify_kind(name)
    if removed:
        return _removed_card(name, index, local_url, job_id, source)
    previewed = local and kind in ("image", "video")
    preview_url = f"{local_url}/preview" if previewed else None
    if (
        previewed
        and kind == "video"
        and not previews.preview_path(job_id, index, kind).is_file()
    ):
        preview_url = local_url
    return AssetCard(
        name=name,
        index=index,
        kind=kind,
        view_url=remote_url or local_url,
        download_url=remote_url or f"{local_url}?dl=1",
        download_name=download_name(name, job_id),
        local_fallback_url=local_url if remote_url else None,
        preview_url=preview_url,
        poster_url=f"{local_url}/poster" if previewed and kind == "video" else None,
        source=source,
    )


def _removed_card(
    name: str, index: int, local_url: str, job_id: str, source: str | None
) -> AssetCard:
    kind = classify_kind(name)
    poster = kind == "video" and previews.poster_path(job_id, index).is_file()
    preview = kind == "image" and previews.preview_path(job_id, index, kind).is_file()
    return AssetCard(
        name=name,
        index=index,
        kind=kind,
        view_url=local_url,
        download_url=f"{local_url}?dl=1",
        download_name=download_name(name, job_id),
        preview_url=f"{local_url}/preview" if preview else None,
        poster_url=f"{local_url}/poster" if poster else None,
        source=source,
        removed=True,
    )


def _removed_positions(job: Job) -> list[int]:
    """The files of a grouped download the sweep removed on purpose, by
    their place in the result. Asked of the database only when a file is
    not on disk."""
    paths = [path for r in job.download_results() for path in r.output_paths]
    if all(path.is_file() for path in paths):
        return []
    return [file.position for file in downloads.removed_in([job.id])]


def job_assets(job: Job, files_prefix: str) -> JobAssets:
    """A finished job's result as asset cards, plus the sources that failed
    (only a grouped download job has any). A file's index counts across every
    group -- it is what `serve_job_file` resolves, so the two must agree."""
    if job.status != "done":
        return JobAssets()
    prefix = f"{files_prefix}/{job.id}"
    if not job.is_grouped:
        assets = [
            asset_card(
                f.name,
                idx,
                prefix,
                remote_url=f.remote_url,
                local=f.path is not None,
                job_id=job.id,
            )
            for idx, f in enumerate(job.result_files())
        ]
        return JobAssets(assets=assets)

    assets, failed_sources = [], []
    removed = _removed_positions(job)
    for r in job.download_results():
        if not r.success:
            failed_sources.append(FailedSource(source=r.url, error=r.error or ""))
            continue
        for path in r.output_paths:
            assets.append(
                asset_card(
                    path.name,
                    len(assets),
                    prefix,
                    r.url,
                    job_id=job.id,
                    removed=len(assets) in removed,
                )
            )
    return JobAssets(assets=assets, failed_sources=failed_sources)
