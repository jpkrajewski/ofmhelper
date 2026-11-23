# Copyright (C) 2026 jpkrajewski

from datetime import UTC, datetime
from pathlib import Path
lazy from collections.abc import Callable

from ofmhelpers.db.cookies import store as login_cookies
from ofmhelpers.db.downloads import store
from ofmhelpers.db.downloads.schema import DownloadedFile
from ofmhelpers.db.jobs.schema import DownloadResult
from ofmhelpers.db.jobs.store import get_job
from ofmhelpers.db.users import store as users
from ofmhelpers.features.assets.store import register_generated_asset
from ofmhelpers.features.kinds import classify_kind
from ofmhelpers.features.previews import render as previews
from ofmhelpers.features.users.viewing import viewer_for
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.jobs.schema import Job

logger = get_logger(__name__)


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
        download_all(wanted, cookies=login_cookies.for_user(user_id)) if wanted else [],
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
                "download job %s: %s not fetched again: %s",
                job_id,
                url,
                new.error,
            )
            results.append(old.model_copy(update={"again_error": new.error}))
        else:
            results.append(new)
    _keep(job, results, Path(assets_root), set(wanted))
    return results


def _keep(
    job: Job | None,
    results: list[DownloadResult],
    assets_root: Path,
    fetched: set[str],
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
                    ),
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
