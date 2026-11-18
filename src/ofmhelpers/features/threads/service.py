"""What /threads does behind its routes (web/routers/image_tools/threads.py):
the same phone cards as /trials, an account's POSTED reels from the last
sweep's read (no Drive call: the page answers at once), and a click on one
reel that starts its job (`tasks.make_pictures`, on the media queue: big
local encodes take turns there), again on a made one for another set
(Regenerate). Each reel shows its latest job: in line, being made, its
pictures and Drive folder, or that it failed. A start is one locked step
(review 2026-11-18: 30 POSTs at once made 30 jobs): one set of a reel at a
time, whatever the clicks, tabs or retries."""

import secrets
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.cache import JobQueue, QueueName
from ofmhelpers.cache.lock import locked
from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import list_batch
from ofmhelpers.db.stores.users import refresh_token
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.features.threads.tasks import (
    TASK,
    Pictures,
    PicturesRun,
    make_pictures,
)
from ofmhelpers.schemas import Job, JobStatus
from ofmhelpers.schemas.drive import DriveBoard, DriveCard
from ofmhelpers.schemas.gdrive import DriveFile

NO_FOLDER = "No such account folder"
NO_REEL = "No such reel"
NO_PICTURE = "No such picture"
NO_DRIVE = "Connect Google Drive first"
# A start's check-then-queue, held at most this long.
_START_LOCK_S = 10


class ReelPictures(BaseModel):
    """One POSTED reel on the page and its latest job (None: never made)."""

    reel: DriveFile
    job: Job | None = None
    # Jobs ahead of it on the media queue while it waits; None = not waiting.
    ahead: int | None = None

    @property
    def making(self) -> bool:
        return self.job is not None and self.job.status is JobStatus.running

    @property
    def failed(self) -> bool:
        return self.job is not None and self.job.status is JobStatus.failed

    @staticmethod
    def made_of(job: Job | None) -> Pictures | None:
        if job is None or job.status is not JobStatus.done:
            return None
        return Pictures.model_validate(job.result)

    @property
    def made(self) -> Pictures | None:
        return self.made_of(self.job)

    @property
    def place(self) -> float:
        """Where it sits on the page, lowest first (owner 2026-11-17: a reel
        clicked "dont go up"): being made, then made, then failed, newest of
        each first (a group is 1e10 s wide, past any timestamp); untouched
        reels last, in POSTED's order (the sort is stable)."""
        if self.job is None:
            return 3e10
        group = {JobStatus.running: 0, JobStatus.done: 1, JobStatus.failed: 2}
        when = self.job.finished_at or self.job.created_at
        return group[self.job.status] * 1e10 - when


class ThreadsFolder(BaseModel):
    card: DriveCard
    reels: list[ReelPictures]
    # The person's Google Drive is connected (a set needs it).
    connected: bool

    @property
    def making(self) -> bool:
        return any(r.making for r in self.reels)


def board(uid: int, scope: int | None) -> DriveBoard:
    """/drive's phone cards from the last sweep's read."""
    return drive_folders.board(owner=scope, viewer=uid, live=False)


class Threads(BaseModel, frozen=True):
    """The signed-in person's /threads of one login."""

    uid: int
    scope: int | None
    login_id: int

    def _card(self) -> DriveCard:
        found = next(
            (
                c
                for c in board(self.uid, self.scope).cards
                if c.target.login_id == self.login_id
            ),
            None,
        )
        if found is None or not found.account_folder_id:
            raise NotFoundError(NO_FOLDER)
        return found

    def _all_jobs(self) -> list[Job]:
        """Every set's job of this login, oldest first."""
        return list_batch(TASK, str(self.login_id), str(self.uid))

    def _jobs(self) -> dict[str, Job]:
        """Each reel's latest job, by its Drive file id."""
        return {job.params["key"]: job for job in self._all_jobs()}

    def open(self) -> ThreadsFolder:
        card = self._card()
        jobs = self._jobs()
        line = JobQueue(name=QueueName.MEDIA).line()
        reels = [
            ReelPictures(
                reel=f,
                job=jobs.get(f.id),
                ahead=line.ahead(jobs[f.id].id) if f.id in jobs else None,
            )
            for f in card.scan.posted
            if f.kind == "video"
        ]
        return ThreadsFolder(
            card=card,
            reels=sorted(reels, key=lambda r: r.place),
            connected=bool(refresh_token(self.uid)),
        )

    def start(self, key: str) -> None:
        """A new set of pictures of reel `key` (its first, or Regenerate: a
        new seed, its own Drive folder), unless one is being made now."""
        card = self._card()
        reel = next(
            (f for f in card.scan.posted if f.id == key and f.kind == "video"), None
        )
        if reel is None:
            raise NotFoundError(NO_REEL)
        if not refresh_token(self.uid):
            raise RefusedError(NO_DRIVE)
        lock = f"threads:start:{self.uid}:{self.login_id}:{key}"
        with locked(lock, _START_LOCK_S):
            latest = self._jobs().get(key)
            if latest is not None and latest.status is JobStatus.running:
                return
            run = PicturesRun(
                login_id=self.login_id,
                key=key,
                reel_name=reel.name,
                account_folder_id=card.account_folder_id or "",
                seed=secrets.token_hex(4),
                preset=settings.threads.preset,
            )
            start_job(
                TASK,
                {"batch": str(self.login_id), **run.model_dump()},
                make_pictures,
                {**run.model_dump(), "user_id": self.uid},
                actor=str(self.uid),
                queue=QueueName.MEDIA,
                job_timeout=settings.threads.job_timeout_s,
            )

    def picture(self, key: str, name: str) -> Path:
        """One picture of the reel's latest set; only that set's names."""
        latest = self._jobs().get(key)
        shown = ReelPictures.made_of(latest)
        if latest is None or shown is None or name not in shown.names:
            raise NotFoundError(NO_PICTURE)
        path = PicturesRun.model_validate(latest.params).here(self.uid) / name
        if not path.is_file():
            raise NotFoundError(NO_PICTURE)
        return path
