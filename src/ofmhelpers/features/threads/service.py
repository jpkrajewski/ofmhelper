# Copyright (C) 2026 jpkrajewski

import secrets
from types import MappingProxyType
lazy from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.cache import JobQueue, QueueName
from ofmhelpers.cache.lock import locked
from ofmhelpers.config import settings
from ofmhelpers.db.drive_folders.models import DriveBoard, DriveCard
from ofmhelpers.db.drive_folders.schema import DriveFile
from ofmhelpers.db.jobs.schema import Job, JobStatus
from ofmhelpers.db.jobs.store import list_batch
from ofmhelpers.db.users.store import refresh_token
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.features.storage.service import StorageServiceFolders
from ofmhelpers.features.threads.tasks import (
    TASK,
    Pictures,
    PicturesRun,
    make_pictures,
)

NO_FOLDER = "No such account folder"
NO_REEL = "No such reel"
NO_PICTURE = "No such picture"
NO_DRIVE = "Connect Google Drive first"
_START_LOCK_S = 10


class ReelPictures(BaseModel):
    reel: DriveFile
    job: Job | None = None
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
        if self.job is None:
            return 3e10
        group = {JobStatus.running: 0, JobStatus.done: 1, JobStatus.failed: 2}
        when = self.job.finished_at or self.job.created_at
        return group[self.job.status] * 1e10 - when


class ThreadsFolder(BaseModel):
    card: DriveCard
    reels: list[ReelPictures]
    connected: bool

    @property
    def making(self) -> bool:
        return any(r.making for r in self.reels)


class ThreadsProgress(BaseModel):
    making: int
    made: int


class PhoneCards(BaseModel):
    board: DriveBoard
    progress: dict[int, ThreadsProgress]


class Threads(BaseModel, frozen=True):
    uid: int
    folders: StorageServiceFolders
    login_id: int

    @classmethod
    def phone_cards(cls, uid: int, folders: StorageServiceFolders) -> PhoneCards:
        cards = folders.saved_board(viewer=uid)
        progress = {}
        for card in cards.cards:
            jobs = list_batch(TASK, str(card.target.login_id), str(uid))
            progress[card.target.login_id] = ThreadsProgress(
                making=sum(1 for job in jobs if job.status is JobStatus.running),
                made=sum(1 for job in jobs if job.status is JobStatus.done),
            )
        return PhoneCards(board=cards, progress=progress)

    def _card(self) -> DriveCard:
        found = next(
            (
                c
                for c in self.folders.saved_board(viewer=self.uid).cards
                if c.target.login_id == self.login_id
            ),
            None,
        )
        if found is None or not found.account_folder_id:
            raise NotFoundError(NO_FOLDER)
        return found

    def _all_jobs(self) -> list[Job]:
        return list_batch(TASK, str(self.login_id), str(self.uid))

    def _jobs(self) -> MappingProxyType[str, Job]:
        return MappingProxyType({job.params["key"]: job for job in self._all_jobs()})

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
        card = self._card()
        reel = next(
            (f for f in card.scan.posted if f.id == key and f.kind == "video"),
            None,
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
        latest = self._jobs().get(key)
        shown = ReelPictures.made_of(latest)
        if latest is None or shown is None or name not in shown.names:
            raise NotFoundError(NO_PICTURE)
        path = PicturesRun.model_validate(latest.params).here(self.uid) / name
        if not path.is_file():
            raise NotFoundError(NO_PICTURE)
        return path
