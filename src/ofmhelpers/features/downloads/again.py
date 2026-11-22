from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ofmhelpers.db.downloads import store
from ofmhelpers.db.jobs.store import get_job, hide_job, running_with_links
from ofmhelpers.features.jobs.schema import JobQueued, error_reason
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.downloads.schema import Download
lazy from ofmhelpers.db.jobs.schema import Job
lazy from ofmhelpers.features.downloads.restarts import RESTARTS

logger = get_logger(__name__)

TASKS = frozenset({"download_media", "download_videos", "download_images"})
NO_DOWNLOAD = "No such download"
NOT_HERE = "Not on this server"


class Seen(BaseModel):
    model_config = ConfigDict(frozen=True)

    new: list[str]
    jobs: list[str]


class StoredFile(BaseModel):
    path: str


def _here(files: list[Download]) -> bool:
    return all(f.stored and Path(f.file_path).is_file() for f in files)


class Again(BaseModel, frozen=True):
    actor: str

    @property
    def _user_id(self) -> int | None:
        return int(self.actor) if self.actor.isdecimal() else None

    def sort_out(self, links: list[str]) -> Seen:
        user_id = self._user_id
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
            hide_job(job_id, self.actor, hidden=False)
            if not _here(files):
                self._fetch_again(job_id)
        waiting = [link for link in links if link not in by_link]
        running = running_with_links(TASKS, self.actor, waiting)
        jobs += [job.id for job in running if job.id not in jobs]
        busy = {url for job in running for url in job.params.get("urls", [])}
        return Seen(new=[link for link in waiting if link not in busy], jobs=jobs)

    def _fetch_again(self, job_id: str) -> Job:
        job = get_job(job_id, self.actor)
        if job is None or job.task not in TASKS:
            raise NotFoundError(NO_DOWNLOAD)
        if job.status == "running" or _here(store.of_job(job.id)):
            return job
        if RESTARTS[job.task].replay(job):
            logger.info("download job %s is fetched again", job.id)
        return job

    def replay(self, job_id: str) -> JobQueued:
        return JobQueued(job_id=self._fetch_again(job_id).id)

    def stored_file(self, job_id: str, position: int) -> StoredFile:
        job = get_job(job_id, self.actor)
        file = store.get(job_id, position) if job is not None else None
        if job is None or file is None:
            raise NotFoundError(NO_DOWNLOAD)
        stored = file.asset_path
        if not file.stored or not stored or not Path(stored).is_file():
            raise NotFoundError(_why_not(job, file.url))
        return StoredFile(path=str(Path(stored).resolve()))


def removed(job_id: str, position: int) -> bool:
    file = store.get(job_id, position)
    return file is not None and not file.stored


def _why_not(job: Job, link: str) -> str:
    failed = next(
        (r.again_error for r in job.download_results() if r.url == link), None
    )
    return error_reason(failed) if failed else NOT_HERE
