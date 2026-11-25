# Copyright (C) 2026 jpkrajewski

import logging
import math
from collections.abc import Callable
from typing import Any, Self
lazy from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ofmhelpers.cache import JobQueue, Line, QueueName
from ofmhelpers.config import settings
from ofmhelpers.db.drive_folders import store as drive_store
from ofmhelpers.db.jobs.store import (
    create_job,
    get_job,
    merge_params,
    reopen_job,
    running_jobs,
)
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.previews.render import PREVIEW_ERRORS
from ofmhelpers.features.storage.service import StorageServiceFolders
from ofmhelpers.features.trials import media, tasks
from ofmhelpers.features.trials.constants import TrialsPhase
from ofmhelpers.features.trials.state import (
    CAPTION_MAX,
    FolderState,
    ReelState,
    ReelStatus,
    at,
    change,
    change_reel,
    load,
)
lazy from ofmhelpers.db.drive_folders.models import DriveBoard, DriveCard
lazy from ofmhelpers.db.jobs.schema import Job

logger = logging.getLogger(__name__)

TASK = "trials"
RUNS: frozendict[str, Callable[..., object]] = frozendict(
    {
        TrialsPhase.GENERATE: tasks.make_trials,
        TrialsPhase.REMAKE: tasks.generate_reels,
        TrialsPhase.CAPTIONS: tasks.write_captions,
        TrialsPhase.UPLOAD: tasks.upload_trials,
    },
)
_RESTARTS_MAX = 2

NO_FOLDER = "No such account folder"
NO_REEL = "No such reel"
NOT_GENERATED = "Not generated yet"
NO_POSTER = "No poster"


class NotFoundError(LookupError): ...


class Started(BaseModel):
    started: bool


class Selected(BaseModel):
    selected: bool


class Uploading(BaseModel):
    uploading: list[str]


def _in_line(ahead: int) -> str:
    return f"In queue · {ahead} ahead" if ahead else "In queue · next"


class FolderProgress(BaseModel):
    ready: int
    total: int
    making: bool
    ahead: int | None = None

    @classmethod
    def of(cls, state: FolderState, running: set[str], line: Line) -> Self:
        return cls(
            ready=len(state.ready),
            total=len(state.reels),
            making=state.job_id in running,
            ahead=line.ahead(state.job_id or ""),
        )

    @property
    def label(self) -> str:
        if self.ahead is not None:
            return _in_line(self.ahead)
        return f"Making… {self.ready} of {self.total}"


class CardStamp(BaseModel):
    key: str
    stamp: str


class FolderLive(BaseModel):
    running: bool
    making: bool
    todo: int
    status: str
    problem: str
    trials_url: str
    cards: list[CardStamp]


def _at_work(uid: int, job_id: str | None) -> bool:
    if not job_id:
        return False
    job = get_job(job_id, str(uid))
    return job is not None and job.status == "running"


class FolderView(BaseModel):
    state: FolderState
    active: set[str]
    making: bool
    busy: set[str]
    uploading: set[str]
    line: Line

    @classmethod
    def of(cls, uid: int, state: FolderState) -> Self:
        ids = {state.job_id, *(r.job_id for r in state.reels)}
        active = {i for i in ids if i and _at_work(uid, i)}
        busy = {r.key for r in state.reels if r.job_id in active}
        return cls(
            state=state,
            active=active,
            making=state.job_id in active,
            busy=busy,
            uploading={
                r.key
                for r in state.reels
                if r.key in busy and r.status == ReelStatus.UPLOADING
            },
            line=JobQueue(name=QueueName.MEDIA).line() if active else Line(),
        )

    @property
    def login_id(self) -> int:
        return self.state.login_id

    @property
    def made(self) -> list[ReelState]:
        return sorted(self.state.ready, key=ReelState.made_or_posted, reverse=True)

    @property
    def pending(self) -> list[ReelState]:
        if not self.making:
            return []
        return [r for r in self.state.todo if r.status != ReelStatus.FAILED]

    @property
    def failed(self) -> list[ReelState]:
        return self.state.failed

    @property
    def ahead(self) -> int | None:
        return self.line.ahead(self.state.job_id or "") if self.making else None

    @property
    def queued(self) -> set[str]:
        return {r.key for r in self.state.reels if r.job_id in self.line}

    @property
    def pending_stamp(self) -> str:
        return "queued" if self.ahead is not None else ""

    @property
    def status(self) -> str:
        if self.ahead is not None:
            return _in_line(self.ahead)
        if self.making:
            ready, total = len(self.state.ready), len(self.state.reels)
            made = f"Making trials… {ready} of {total} ready"
            failed = len(self.state.failed)
            return f"{made}, {failed} failed" if failed else made
        if self.uploading:
            return self._upload_status()
        waiting = [
            ahead
            for r in self.state.reels
            if r.job_id and (ahead := self.line.ahead(r.job_id)) is not None
        ]
        if waiting:
            return _in_line(min(waiting))
        if self.busy:
            return f"Making {len(self.busy)}…"
        return self.state.note or ""

    def _upload_status(self) -> str:
        reels = [r for r in self.state.reels if r.key in self.uploading]
        ahead = [
            a
            for r in reels
            if r.job_id and (a := self.line.ahead(r.job_id)) is not None
        ]
        if ahead:
            return _in_line(min(ahead))
        remade = sum(not r.stored for r in reels)
        if remade:
            minutes = math.ceil(remade * settings.trials.remake_s / 60)
            copies = "copy" if remade == 1 else "copies"
            return f"Making {remade} {copies} again for Drive · about {minutes} min"
        return f"Uploading {len(reels)} to Drive…"

    def stamp(self, reel: ReelState) -> str:
        busy: str | bool = reel.key in self.busy
        if reel.key in self.uploading:
            busy = "uploading"
        elif reel.key in self.queued:
            busy = "queued"
        choices = len(reel.captions)
        return (
            f"{reel.made}.{reel.status}.{busy}.{reel.approved}.{choices}"
            f".{reel.stored}.{reel.error or ''}"
        )

    def made_among(self, keys: set[str]) -> list[ReelState]:
        return [r for r in self.made if r.key in keys]

    def pending_among(self, keys: set[str]) -> list[ReelState]:
        return [r for r in self.pending if f"pending-{r.key}" in keys]

    def failed_among(self, keys: set[str]) -> list[ReelState]:
        return [r for r in self.failed if f"failed-{r.key}" in keys]

    def live(self) -> FolderLive:
        cards = [CardStamp(key=r.key, stamp=self.stamp(r)) for r in self.made]
        cards += [
            CardStamp(key=f"pending-{r.key}", stamp=self.pending_stamp)
            for r in self.pending
        ]
        cards += [
            CardStamp(key=f"failed-{r.key}", stamp=r.error or "") for r in self.failed
        ]
        return FolderLive(
            running=bool(self.active),
            making=self.making,
            todo=len(self.state.todo),
            status=self.status,
            problem=self.state.problem or "",
            trials_url=self.state.trials_url or "",
            cards=cards,
        )


def _card(folders: StorageServiceFolders, viewer: int, login_id: int) -> DriveCard:
    board = folders.saved_board(viewer=viewer)
    card = next((c for c in board.cards if c.target.login_id == login_id), None)
    if card is None:
        raise NotFoundError(NO_FOLDER)
    return card


STOPPED = "Stopped by an update: upload again"


def _released(uid: int, state: FolderState) -> None:
    for reel in state.reels:
        if not reel.job_id or _at_work(uid, reel.job_id):
            continue
        if reel.status == ReelStatus.UPLOADING:
            job = get_job(reel.job_id, str(uid))
            reel.upload_failed(job.error if job and job.error else STOPPED)
        else:
            reel.job_id = None


def _synced(card: DriveCard, state: FolderState) -> None:
    state.account_folder_id = card.account_folder_id or ""
    state.posted_folder_id = card.posted_folder_id or ""
    if not card.posted_folder_id or card.error or card.unread:
        return
    tasks.merge_posted(state, [f for f in card.scan.posted if f.kind == "video"])


class _Synced(BaseModel):
    card: DriveCard
    state: FolderState


def _sync_state(folders: StorageServiceFolders, viewer: int, login_id: int) -> _Synced:
    card = _card(folders, viewer, login_id)
    state = load(viewer, login_id)
    before = state.model_dump_json()

    def fixed(s: FolderState) -> None:
        _synced(card, s)
        _released(viewer, s)

    fixed(state)
    if state.model_dump_json() != before:
        state = change(viewer, login_id, fixed)
    return _Synced(card=card, state=state)


def read_posted(folders: StorageServiceFolders, uid: int, login_id: int) -> bool:
    try:
        card = _sync_state(folders, uid, login_id).card
    except NotFoundError:  # silent: a login with no card any more has nothing to read
        logger.info("trials: no folder card for login %s", login_id)
        return False
    return bool(card.posted_folder_id) and not card.error and not card.unread


class PhoneCards(BaseModel):
    board: DriveBoard
    progress: dict[int, FolderProgress]


class OpenedFolder(BaseModel):
    card: DriveCard
    view: FolderView


def phone_cards(uid: int, folders: StorageServiceFolders) -> PhoneCards:
    board = folders.saved_board(viewer=uid)
    running = {j.id for j in running_jobs() if j.actor == str(uid)}
    line = JobQueue(name=QueueName.MEDIA).line()
    progress = {
        c.target.login_id: FolderProgress.of(
            load(uid, c.target.login_id),
            running,
            line,
        )
        for c in board.cards
    }
    return PhoneCards(board=board, progress=progress)


class _Claim:
    def __init__(self, uid: int, login_id: int, label: str) -> None:
        self.uid, self.login_id, self.label = uid, login_id, label
        self.job_id: str | None = None

    def job(self) -> str:
        if self.job_id is None:
            self.job_id = create_job(
                TASK,
                {"label": self.label, "login": self.login_id},
                actor=str(self.uid),
            )
        return self.job_id

    def launch(
        self,
        enqueue: Callable[..., object],
        fn: Callable[..., object],
        reels: int,
        task_args: dict[str, Any] | None = None,
        *,
        at_front: bool = False,
    ) -> None:
        trials = settings.trials
        if self.job_id is None:
            return
        task_args = task_args or {}
        merge_params(self.job_id, {"args": task_args})
        enqueue(
            run_job,
            (
                self.job_id,
                fn,
                {"user_id": self.uid, "login_id": self.login_id, **task_args},
            ),
            job_timeout=trials.job_grace_s + trials.per_reel_s * max(1, reels),
            at_front=at_front,
        )


class TrialsRun(BaseModel):
    model_config = ConfigDict(extra="allow")

    user_id: int
    login_id: int


class RemakeArgs(BaseModel):
    keys: list[str]


class UploadArgs(BaseModel):
    keys: list[str]
    account_folder_id: str


def replay_run(job: Job) -> TrialsRun | None:
    args = job.params.get("args")
    if job.actor is None or not isinstance(args, dict):
        return None
    uid = int(job.actor)
    return TrialsRun.model_validate(
        {
            "user_id": uid,
            "login_id": int(job.params["login"]),
            **args,
        },
    )


def restart(job: Job) -> bool:
    restarts = int(job.params.get("restarts", 0))
    label = job.params.get("label")
    args = job.params.get("args")
    if (
        label not in (TrialsPhase.GENERATE, TrialsPhase.REMAKE, TrialsPhase.UPLOAD)
        or job.actor is None
        or restarts >= _RESTARTS_MAX
        or (label != TrialsPhase.GENERATE and not isinstance(args, dict))
    ):
        return False
    remake = RemakeArgs.model_validate(args) if label == TrialsPhase.REMAKE else None
    upload = UploadArgs.model_validate(args) if label == TrialsPhase.UPLOAD else None
    uid, login_id = int(job.actor), int(job.params["login"])
    merge_params(job.id, {"restarts": restarts + 1})
    reopen_job(job.id)
    claim = _Claim(uid, login_id, str(label))
    claim.job_id = job.id
    if remake is not None:
        claim.launch(
            JobQueue(name=QueueName.MEDIA).put,
            tasks.generate_reels,
            len(remake.keys),
            {"keys": remake.keys},
        )
        return True
    if upload is None:
        claim.launch(
            JobQueue(name=QueueName.MEDIA).put,
            tasks.make_trials,
            len(load(uid, login_id).reels),
        )
        return True
    stored = {r.key for r in load(uid, login_id).reels if r.stored}
    remade = set(upload.keys) - stored
    claim.launch(
        JobQueue(name=QueueName.MEDIA if remade else QueueName.DEFAULT).put,
        tasks.upload_trials,
        len(upload.keys),
        {"account_folder_id": upload.account_folder_id, "keys": upload.keys},
    )
    return True


class Trials(BaseModel, frozen=True):
    uid: int
    scope: int | None
    folders: StorageServiceFolders
    login_id: int
    encode: Callable[..., object]
    enqueue: Callable[..., object]

    def owned(self) -> None:
        if all(t.login_id != self.login_id for t in drive_store.targets(self.scope)):
            raise NotFoundError(NO_FOLDER)

    def open(self) -> OpenedFolder:
        synced = _sync_state(self.folders, self.uid, self.login_id)
        return OpenedFolder(
            card=synced.card,
            view=FolderView.of(self.uid, synced.state),
        )

    def live(self) -> FolderLive:
        return FolderView.of(self.uid, load(self.uid, self.login_id)).live()

    def view(self) -> FolderView:
        self.owned()
        return FolderView.of(self.uid, load(self.uid, self.login_id))

    def claim(self, label: str) -> _Claim:
        return _Claim(self.uid, self.login_id, label)

    def busy(self, job_id: str | None) -> bool:
        return _at_work(self.uid, job_id)

    def generate(self) -> Started:
        _sync_state(self.folders, self.uid, self.login_id)
        claim = self.claim(TrialsPhase.GENERATE)

        def take(state: FolderState) -> None:
            if not self.busy(state.job_id):
                state.job_id = claim.job()
                state.tell(None)
                for reel in state.failed:
                    reel.try_again()

        state = change(self.uid, self.login_id, take)
        if claim.job_id:
            claim.launch(self.encode, tasks.make_trials, len(state.reels))
        return Started(started=bool(claim.job_id))

    def upload(self, approved: list[str] | None) -> Uploading:
        wanted = set(approved or [])
        claim = self.claim(TrialsPhase.UPLOAD)
        claimed: list[str] = []

        def take(state: FolderState) -> None:
            if not state.account_folder_id or any(
                r.status == ReelStatus.UPLOADING and self.busy(r.job_id)
                for r in state.reels
            ):
                return
            for reel in state.ready:
                if reel.key in wanted and not self.busy(reel.job_id):
                    reel.claim_upload(claim.job())
                    claimed.append(reel.key)

        state = change(self.uid, self.login_id, take)
        if claim.job_id:
            remade = any(not r.stored for r in state.reels if r.key in claimed)
            claim.launch(
                self.encode if remade else self.enqueue,
                tasks.upload_trials,
                len(claimed),
                {"account_folder_id": state.account_folder_id, "keys": claimed},
                at_front=True,
            )
        return Uploading(uploading=claimed)


class Reel(BaseModel, frozen=True):
    trials: Trials
    key: str

    def _change(self, edit: Callable[[ReelState], None]) -> FolderState:
        state = change_reel(self.trials.uid, self.trials.login_id, self.key, edit)
        if state.reel(self.key) is None:
            raise NotFoundError(NO_REEL)
        return state

    def remake(self, caption: str | None = None) -> Started:
        claim = self.trials.claim(TrialsPhase.REMAKE)

        def take(reel: ReelState) -> None:
            if not self.trials.busy(reel.job_id):
                reel.claim_remake(claim.job(), caption)

        self._change(take)
        if claim.job_id:
            claim.launch(
                self.trials.encode,
                tasks.generate_reels,
                1,
                {"keys": [self.key]},
                at_front=True,
            )
        return Started(started=bool(claim.job_id))

    def ask_captions(self) -> Started:
        claim = self.trials.claim(TrialsPhase.CAPTIONS)

        def take(reel: ReelState) -> None:
            if not reel.captions and not self.trials.busy(reel.job_id):
                reel.claim_captions(claim.job())

        self._change(take)
        if claim.job_id:
            claim.launch(
                self.trials.enqueue,
                tasks.write_captions,
                1,
                {"key": self.key},
            )
        return Started(started=bool(claim.job_id))

    def apply_caption(self, caption: str) -> Started:
        text = " ".join(caption.split())[:CAPTION_MAX]
        if not text:
            return Started(started=False)
        return self.remake(text)

    def select(self, selected: bool) -> Selected:  # noqa: FBT001
        self._change(lambda r: r.select(selected))
        return Selected(selected=selected)

    def _copy(self) -> Path:
        reel = load(self.trials.uid, self.trials.login_id).reel(self.key)
        path = at(self.trials.uid, self.trials.login_id, reel.out) if reel else None
        if path is None or not path.is_file():
            raise NotFoundError(NOT_GENERATED)
        return path

    def playable(self) -> Path:
        copy = self._copy()
        try:
            return media.preview(copy)
        except PREVIEW_ERRORS:
            logger.warning("trials: preview of %s failed", copy, exc_info=True)
            return copy

    def poster(self) -> Path:
        reel = load(self.trials.uid, self.trials.login_id).reel(self.key)
        gone = (
            at(self.trials.uid, self.trials.login_id, reel.out)
            if reel and not reel.stored
            else None
        )
        if gone is not None and (kept := media.poster_path(gone)).is_file():
            return kept
        copy = self._copy()
        try:
            return media.poster(copy)
        except PREVIEW_ERRORS as exc:
            logger.warning("trials: poster of %s failed", copy, exc_info=True)
            raise NotFoundError(NO_POSTER) from exc
