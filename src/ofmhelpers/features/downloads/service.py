from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.db.cookies import store as login_cookies
from ofmhelpers.db.downloads import store
from ofmhelpers.db.downloads.schema import (
    DownloadedFile,
    DownloadResult,
    DownloadsQueued,
)
from ofmhelpers.db.jobs.store import create_job, get_job
from ofmhelpers.db.users import store as users
from ofmhelpers.features.assets.store import register_generated_asset
from ofmhelpers.features.downloads import again
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.kinds import classify_kind
from ofmhelpers.features.previews import render as previews
from ofmhelpers.features.refusals import RefusedError, input_refused
from ofmhelpers.features.users.viewing import viewer_for
from ofmhelpers.integrations.downloaders.urls import read_links
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.jobs.schema import Job

logger = get_logger(__name__)


def queue_downloads(
    task: str,
    pasted: str,
    run: Callable[..., Any],
    actor: str,
    owner_id: int,
    assets_root: Path,
) -> DownloadsQueued:
    with input_refused():
        links = list(dict.fromkeys(read_links(pasted)))
    if not links:
        msg = "At least one URL is required"
        raise RefusedError(msg)
    seen = again.sort_out(actor, links)
    if not seen.new:
        return DownloadsQueued(again=seen.jobs)
    job_id = Links(
        task=task, run=run, actor=actor, owner_id=owner_id, assets_root=assets_root
    ).queue(seen.new)
    return DownloadsQueued(job_id=job_id, job_ids=[job_id], again=seen.jobs)


class Links(BaseModel, frozen=True):
    task: str
    run: Callable[..., Any]
    actor: str
    owner_id: int
    assets_root: Path

    def queue(self, links: list[str], **noted: str) -> str:
        job_id = create_job(self.task, {"urls": links, **noted}, actor=self.actor)
        enqueue(
            run_job,
            job_id,
            self.run,
            {
                "urls": links,
                "assets_root": str(self.assets_root),
                "user_id": self.owner_id,
                "job_id": job_id,
            },
        )
        return job_id


def _on_disk(result: DownloadResult | None) -> bool:
    if result is None or not result.output_paths:
        return False
    return all(path.is_file() for path in result.output_paths)


def _team_of(user_id: int | None) -> frozenset[int]:
    user = users.get_user(user_id) if user_id is not None else None
    return viewer_for(user, None).team_ids if user is not None else frozenset()


def _already_here(user_id: int | None, urls: list[str]) -> list[DownloadResult]:
    if not urls:
        return []
    found: dict[str, list[Path]] = {}
    for file in store.stored_by(_team_of(user_id), urls):
        found.setdefault(file.url, []).append(Path(file.file_path))
    return [
        DownloadResult(url=url, success=True, output_paths=paths)
        for url, paths in found.items()
        if all(path.is_file() for path in paths)
    ]


def fetch(
    download_all: Callable[..., list[DownloadResult]],
    urls: list[str],
    assets_root: str,
    user_id: int | None,
    job_id: str | None,
) -> list[DownloadResult]:
    job = get_job(job_id) if job_id else None
    before = {r.url: r for r in job.download_results() if r.success} if job else {}
    missing = [url for url in urls if not _on_disk(before.get(url))]
    if job is not None:
        before |= {r.url: r for r in _already_here(job.user_id or user_id, missing)}
    wanted = [url for url in missing if not _on_disk(before.get(url))]
    fetched = iter(
        download_all(wanted, cookies=login_cookies.for_user(user_id)) if wanted else []
    )
    results: list[DownloadResult] = []
    for url in urls:
        old = before.get(url)
        if old is not None and _on_disk(old):
            results.append(old)
            continue
        new = next(fetched)
        if not new.success and old is not None:
            logger.warning(
                "download job %s: %s not fetched again: %s", job_id, url, new.error
            )
            results.append(old.model_copy(update={"again_error": new.error}))
        else:
            results.append(new)
    _keep(job, results, Path(assets_root), set(wanted))
    return results


def _keep(
    job: Job | None, results: list[DownloadResult], assets_root: Path, fetched: set[str]
) -> None:
    files = []
    position = 0
    for result in results:
        for path in result.output_paths:
            if path.is_file():
                asset = register_generated_asset(path, assets_root)
                files.append(
                    DownloadedFile(
                        url=result.url,
                        position=position,
                        file_path=str(path),
                        asset_path=None if asset is None else str(asset),
                    )
                )
                if job is not None and result.url in fetched:
                    _drop_previews(job.id, position, path)
            position += 1
    if job is None or job.user_id is None:
        return
    store.record(job.user_id, job.id, files, datetime.now(UTC))
    store.drop_from(job.id, position)


def _drop_previews(job_id: str, position: int, path: Path) -> None:
    kind = classify_kind(path.name)
    for stale in (
        previews.preview_path(job_id, position, kind),
        previews.poster_path(job_id, position),
        previews.failed_path(job_id, position),
    ):
        stale.unlink(missing_ok=True)
