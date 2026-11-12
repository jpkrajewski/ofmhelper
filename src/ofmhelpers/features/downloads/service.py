"""What the download tools share: a pasted box becomes one queued job for the
links not downloaded before, a job's run fetches what is not on disk and
writes one `downloads` row per file, and the answer that sends the page on
to another one."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.db.stores import cookies as login_cookies
from ofmhelpers.db.stores import downloads as store
from ofmhelpers.db.stores.jobs import create_job, get_job
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.store import register_generated_asset
from ofmhelpers.features.downloads import again
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.previews import render as previews
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.integrations.downloaders.urls import read_links
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.downloads import (
    DownloadedFile,
    DownloadResult,
    DownloadsQueued,
)
lazy from ofmhelpers.schemas import Job

logger = get_logger(__name__)


class Redirect(BaseModel):
    """The URL the page opens next, with the picked videos on its query."""

    redirect: str


def queue_downloads(
    task: str,
    pasted: str,
    run: Callable[..., Any],
    actor: str,
    owner_id: int,
    assets_root: Path,
) -> DownloadsQueued:
    """One job of `task` for the pasted links this person has not downloaded
    before, run by `run` (`run_downloads` of the tool's own module: RQ
    stores that path). A link repeated in the paste counts once. A link
    downloaded before starts nothing new (`again.sort_out`): its card goes
    to the first place, and its video is fetched into its own job again if
    it had been removed. Every link downloaded before: no job at all."""
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
    """A download job to be: whose it is and what runs it. `queue` makes the
    job of `task` for the links and puts its run in line."""

    task: str
    run: Callable[..., Any]
    actor: str
    owner_id: int
    assets_root: Path

    def queue(self, links: list[str], **noted: str) -> str:
        """The new job's id. `noted`: what else its row says about where the
        links came from (`params`, next to `urls`)."""
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
    """Whether a link's earlier download is still whole on this server."""
    if result is None or not result.output_paths:
        return False
    return all(path.is_file() for path in result.output_paths)


def _already_here(urls: list[str]) -> dict[str, DownloadResult]:
    """`urls` someone's download still has whole on disk, as this job's own
    result for that link (dev 2026-11-11: a VA pasted a reel their owner
    had downloaded five hours before and it was fetched again). The asker
    gets a card and a copy in their own asset store; they never see whose
    download it was."""
    if not urls:
        return {}
    found: dict[str, list[Path]] = {}
    for file in store.stored_anywhere(urls):
        found.setdefault(file.url, []).append(Path(file.file_path))
    return {
        url: DownloadResult(url=url, success=True, output_paths=paths)
        for url, paths in found.items()
        if all(path.is_file() for path in paths)
    }


def fetch(
    download_all: Callable[..., list[DownloadResult]],
    urls: list[str],
    assets_root: str,
    user_id: int | None,
    job_id: str | None,
) -> list[DownloadResult]:
    """A download job's run, the same on its first run and on every replay
    (a restart, the card's Download, the link pasted again): a link whose
    files are on disk -- this job's, or anyone's download of it -- is
    not fetched, the rest are, logged in as `user_id`
    (their own cookies.txt, read here: never in the queued job). Every file
    then goes into the asset store and gets its `downloads` row. A link
    that had been downloaded and cannot be fetched again keeps its entry:
    its card and poster stay, its video stays removed.
    `job_id` None: a job queued before 2026-11-11, which keeps no rows."""
    job = get_job(job_id) if job_id else None
    before = {r.url: r for r in job.download_results() if r.success} if job else {}
    missing = [url for url in urls if not _on_disk(before.get(url))]
    if job is not None:
        # A link the server already holds, whoever downloaded it, is that
        # file, not a second download. (A job from before `downloads`
        # existed keeps no rows and looks nothing up.)
        before |= _already_here(missing)
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
            results.append(old)
        else:
            results.append(new)
    _keep(job, results, Path(assets_root), set(wanted))
    return results


def _keep(
    job: Job | None, results: list[DownloadResult], assets_root: Path, fetched: set[str]
) -> None:
    """Every file on disk into the asset store (the Reuse picker's, and what
    a clone names) and into `downloads`, at its place among the job's files.
    The previews of a link fetched just now are dropped: the job's finish
    makes them again from the new file."""
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
