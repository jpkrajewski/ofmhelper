# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel

from ofmhelpers.db.downloads import store as downloads
from ofmhelpers.features.assets.names import download_name
from ofmhelpers.features.jobs.schema import AssetCard, FailedSource, error_reason
from ofmhelpers.features.kinds import classify_kind
from ofmhelpers.features.previews import render as previews
lazy from ofmhelpers.db.jobs.schema import Job


class JobAssets(BaseModel):
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
    again_error: str | None = None,
) -> AssetCard:
    local_url = f"{files_prefix}/{index}"
    kind = classify_kind(name)
    if removed:
        card = _removed_card(name, index, local_url, job_id, source)
        if again_error is None:
            return card
        return card.model_copy(
            update={
                "removed_reason": error_reason(again_error),
                "removed_error": again_error,
            },
        )
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
    name: str,
    index: int,
    local_url: str,
    job_id: str,
    source: str | None,
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
    paths = [path for r in job.download_results() for path in r.output_paths]
    if all(path.is_file() for path in paths):
        return []
    return [file.position for file in downloads.removed_in([job.id])]


def job_assets(job: Job, files_prefix: str) -> JobAssets:
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
                    again_error=r.again_error,
                ),
            )
    return JobAssets(assets=assets, failed_sources=failed_sources)
