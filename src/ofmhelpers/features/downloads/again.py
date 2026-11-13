"""A download asked for again: the same link pasted once more, the card's
Download on a video the retention sweep removed (`cleanup.py`), a clone's
Recreate that needs it back. Nothing is downloaded twice: the job that
fetched the link is found (`downloads`), and only when its video is gone is
that same job run again from its stored links (`service.fetch` skips what is
on disk)."""

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ofmhelpers.db.stores import downloads as store
from ofmhelpers.db.stores.jobs import get_job, hide_job
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import JobQueued
from ofmhelpers.schemas.errors import error_reason
lazy from ofmhelpers.features.jobs.registry import RESTARTS
lazy from ofmhelpers.schemas import Job
lazy from ofmhelpers.schemas.downloads import Download

logger = get_logger(__name__)

# The tasks whose jobs keep `downloads` rows.
TASKS = frozenset({"download_media", "download_videos", "download_images"})
NO_DOWNLOAD = "No such download"
NOT_HERE = "Not on this server"


class Seen(BaseModel):
    """A paste sorted out: the links never downloaded (`new`) and the jobs
    that hold the others (`jobs`)."""

    model_config = ConfigDict(frozen=True)

    new: list[str]
    jobs: list[str]


class StoredFile(BaseModel):
    """Where a download's file is in the asker's part of the asset store."""

    path: str


def _here(files: list[Download]) -> bool:
    return all(f.stored and Path(f.file_path).is_file() for f in files)


def _user_id(actor: str) -> int | None:
    return int(actor) if actor.isdecimal() else None


def sort_out(actor: str, links: list[str]) -> Seen:
    """Which of `links` `actor` has downloaded before (their own downloads
    only). Each of those counts as asked for now (`last_used_at`: its card
    goes first, and the sweep leaves it a day), its card is shown again if
    it was hidden, and a job whose video is gone is run again."""
    user_id = _user_id(actor)
    found = store.newest_of(user_id, links) if user_id is not None else []
    by_link: dict[str, list[Download]] = {}
    for file in found:
        by_link.setdefault(file.url, []).append(file)
    now = datetime.now(UTC)
    jobs: list[str] = []
    for link in links:
        files = by_link.get(link)
        if not files:
            continue
        job_id = files[0].job_id
        store.touch(job_id, link, now)
        if job_id in jobs:
            continue
        jobs.append(job_id)
        hide_job(job_id, actor, hidden=False)
        if not _here(files):
            _fetch_again(job_id, actor)
    return Seen(new=[link for link in links if link not in by_link], jobs=jobs)


def _fetch_again(job_id: str, actor: str) -> Job:
    """The job run again when one of its files is not on this server; one
    that is running already, or has everything, is left as it is (a second
    click, a second file of the same job)."""
    job = get_job(job_id, actor)
    if job is None or job.task not in TASKS:
        raise NotFoundError(NO_DOWNLOAD)
    if job.status == "running" or _here(store.of_job(job.id)):
        return job
    if RESTARTS[job.task].replay(job):
        logger.info("download job %s is fetched again", job.id)
    return job


def replay(job_id: str, actor: str) -> JobQueued:
    """The card's Download on a removed video: the job the page then polls.
    `actor`'s own jobs only."""
    return JobQueued(job_id=_fetch_again(job_id, actor).id)


def removed(job_id: str, position: int) -> bool:
    """Whether the job's file at `position` was removed on purpose (the
    sweep): its Download fetches it again."""
    file = store.get(job_id, position)
    return file is not None and not file.stored


def stored_file(job_id: str, position: int, actor: str) -> StoredFile:
    """Where `actor`'s download now is in the asset store: what a clone's
    Recreate puts in its picker once the video is back (a video fetched
    again may differ by a byte, and so may its stored name). Not back: the
    refusal says why the last fetch got nothing, when one was tried."""
    job = get_job(job_id, actor)
    file = store.get(job_id, position) if job is not None else None
    if job is None or file is None:
        raise NotFoundError(NO_DOWNLOAD)
    if not file.stored or not file.asset_path or not Path(file.asset_path).is_file():
        raise NotFoundError(_why_not(job, file.url))
    return StoredFile(path=str(Path(file.asset_path).resolve()))


def _why_not(job: Job, link: str) -> str:
    """The short reason `link` was not fetched again, `NOT_HERE` when no
    fetch was tried."""
    failed = next(
        (r.again_error for r in job.download_results() if r.url == link), None
    )
    return error_reason(failed) if failed else NOT_HERE
