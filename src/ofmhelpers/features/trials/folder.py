"""One viewer's trials of one login, as the /trials page needs them: what the
page shows right now (`FolderView`) and the claims that start its jobs. All of
it answers at once: the Drive and ffmpeg work is a job (`tasks`). A claim
checks and takes its reels in one locked write: ten clicks at once start one
job, the rest do nothing. What encodes waits its turn on the media queue, the
rest runs beside it; a reel being made or uploaded is the only thing blocked."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from pydantic import BaseModel

from ofmhelpers.cache import JobQueue, QueueName
from ofmhelpers.config import settings
from ofmhelpers.db.stores import drive_folders as drive_store
from ofmhelpers.db.stores.jobs import (
    create_job,
    get_job,
    merge_params,
    reopen_job,
    run_job,
    running_jobs,
)
from ofmhelpers.db.stores.users import refresh_token
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.previews.render import PREVIEW_ERRORS
from ofmhelpers.features.trials import media, tasks
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
from ofmhelpers.log import get_logger
lazy from ofmhelpers.schemas import Job
lazy from ofmhelpers.schemas.drive import DriveBoard, DriveCard

logger = get_logger(__name__)

TASK = "trials"
GENERATE = "Making trials"
# A Generate cut off by restarts this often is failed: something kills it.
_RESTARTS_MAX = 2

NO_FOLDER = "No such account folder"
NO_REEL = "No such reel"
NOT_GENERATED = "Not generated yet"
NO_POSTER = "No poster"


class NotFoundError(LookupError):
    """What was asked for is not there; the text is the page's 404."""


class Started(BaseModel):
    started: bool


class Selected(BaseModel):
    selected: bool


class Uploading(BaseModel):
    uploading: list[str]


def _in_line(ahead: int) -> str:
    return f"In queue · {ahead} ahead" if ahead else "In queue · next"


class FolderProgress(BaseModel):
    """A phone card's trials: copies made, reels in all, Generate running, and
    how many jobs are before it while it waits in the queue."""

    ready: int
    total: int
    making: bool
    ahead: int | None = None

    @classmethod
    def of(cls, state: FolderState, running: set[str], line: dict[str, int]) -> Self:
        return cls(
            ready=len(state.ready),
            total=len(state.reels),
            making=state.job_id in running,
            ahead=line.get(state.job_id or ""),
        )

    @property
    def label(self) -> str:
        """The card's button while Generate runs."""
        if self.ahead is not None:
            return _in_line(self.ahead)
        return f"Making… {self.ready} of {self.total}"


class CardStamp(BaseModel):
    key: str
    stamp: str


class FolderLive(BaseModel):
    """What the page polls: whether anything runs, the status line, and each
    card in page order with its stamp ("pending-<key>" for one still being
    made by Generate trials)."""

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
    """A folder right now: its state and which of its jobs are at work,
    Generate trials and each reel's own (Apply caption / Regenerate / its
    upload)."""

    state: FolderState
    active: set[str]
    making: bool
    busy: set[str]
    uploading: set[str]
    # The media queue: app job id -> jobs ahead of it.
    line: dict[str, int]

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
            line=JobQueue(QueueName.MEDIA).line() if active else {},
        )

    @property
    def login_id(self) -> int:
        return self.state.login_id

    @property
    def pending(self) -> list[ReelState]:
        """The reels Generate trials is still making (shown as generating)."""
        if not self.making:
            return []
        return [r for r in self.state.todo if r.status != ReelStatus.FAILED]

    @property
    def ahead(self) -> int | None:
        """Jobs before this folder's Generate, while it waits in the queue."""
        return self.line.get(self.state.job_id or "") if self.making else None

    @property
    def queued(self) -> set[str]:
        """The reels whose own job (Regenerate, Apply caption) waits in the queue."""
        return {r.key for r in self.state.reels if r.job_id in self.line}

    @property
    def pending_stamp(self) -> str:
        """Moves when the pending cards stop waiting in the queue."""
        return "queued" if self.ahead is not None else ""

    @property
    def status(self) -> str:
        """The page's status line."""
        if self.ahead is not None:
            return _in_line(self.ahead)
        if self.making:
            ready, total = len(self.state.ready), len(self.state.reels)
            made = f"Making trials… {ready} of {total} ready"
            failed = len(self.state.failed)
            return f"{made}, {failed} failed" if failed else made
        if self.uploading:
            return f"Uploading {len(self.uploading)} to Drive…"
        waiting = [
            self.line[r.job_id]
            for r in self.state.reels
            if r.job_id and r.job_id in self.line
        ]
        if waiting:
            return _in_line(min(waiting))
        if self.busy:
            return f"Making {len(self.busy)}…"
        return self.state.note or ""

    def stamp(self, reel: ReelState) -> str:
        """Changes whenever the reel's card would look different."""
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
        return [r for r in self.state.ready if r.key in keys]

    def pending_among(self, keys: set[str]) -> list[ReelState]:
        return [r for r in self.pending if f"pending-{r.key}" in keys]

    def live(self) -> FolderLive:
        """Cards in page order: what is being made is the newest in POSTED."""
        cards = [
            CardStamp(key=f"pending-{r.key}", stamp=self.pending_stamp)
            for r in self.pending
        ]
        cards += [CardStamp(key=r.key, stamp=self.stamp(r)) for r in self.state.ready]
        return FolderLive(
            running=bool(self.active),
            making=self.making,
            todo=len(self.state.todo),
            status=self.status,
            problem=self.state.problem or "",
            trials_url=self.state.trials_url or "",
            cards=cards,
        )


def _card(scope: int | None, viewer: int, login_id: int) -> DriveCard:
    """The login's /drive card from the last sweep's read (no Drive call:
    the page answers at once; Generate trials lists POSTED itself)."""
    board = drive_folders.board(owner=scope, viewer=viewer, live=False)
    card = next((c for c in board.cards if c.target.login_id == login_id), None)
    if card is None:
        raise NotFoundError(NO_FOLDER)
    return card


def _synced(card: DriveCard, state: FolderState) -> None:
    """`state` matched to the card's POSTED videos: new ones added, ones
    gone (or never videos) dropped unless already made."""
    state.account_folder_id = card.account_folder_id or ""
    state.posted_folder_id = card.posted_folder_id or ""
    if not card.posted_folder_id or card.error or card.unread:
        return
    tasks.merge_posted(state, [f for f in card.scan.posted if f.kind == "video"])


def _sync_state(
    scope: int | None, viewer: int, login_id: int
) -> tuple[DriveCard, FolderState]:
    """The login's card and its state matched to POSTED (written only when
    that changed something)."""
    card = _card(scope, viewer, login_id)
    state = load(viewer, login_id)
    before = state.model_dump_json()
    _synced(card, state)
    if state.model_dump_json() != before:
        state = change(viewer, login_id, lambda s: _synced(card, s))
    return card, state


def read_posted(uid: int, login_id: int) -> None:
    """The folder's state matched to the last read of its POSTED (no Drive
    call), for work that has no page open (`cleanup`). A login with no card
    any more: nothing is read."""
    try:
        _sync_state(uid, uid, login_id)
    except NotFoundError:
        logger.info("trials: no folder card for login %s", login_id)


def phone_cards(
    uid: int, scope: int | None
) -> tuple[DriveBoard, dict[int, FolderProgress]]:
    """/drive's last read and each card's trials."""
    board = drive_folders.board(owner=scope, viewer=uid, live=False)
    running = {j.id for j in running_jobs() if j.actor == str(uid)}
    line = JobQueue(QueueName.MEDIA).line()
    progress = {
        c.target.login_id: FolderProgress.of(
            load(uid, c.target.login_id), running, line
        )
        for c in board.cards
    }
    return board, progress


class _Claim:
    """One click's try at a job: the job is made the moment a locked write
    first takes something, and only then goes on the queue."""

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
        self, enqueue: Callable[..., object], fn: Callable, reels: int, **kwargs
    ) -> None:
        trials = settings.trials
        enqueue(
            run_job,
            self.job_id,
            fn,
            {"user_id": self.uid, "login_id": self.login_id, **kwargs},
            job_timeout=trials.job_grace_s + trials.per_reel_s * max(1, reels),
        )


def restart(job: Job) -> bool:
    """A Generate cut off by a restart, queued again (recovery,
    features/jobs/recovery.py): a deploy gives workers 120 s and a Generate
    runs for hours (prod 2026-11-07: one cut at 52 min, "Interrupted by a
    restart"). Copies already made are left alone, so it goes on where it
    stopped. False (failed as interrupted): not a Generate (one reel's
    remake or captions: it does not know its keys), or cut off too often."""
    restarts = int(job.params.get("restarts", 0))
    if (
        job.params.get("label") != GENERATE
        or job.actor is None
        or restarts >= _RESTARTS_MAX
    ):
        return False
    uid, login_id = int(job.actor), int(job.params["login"])
    # Counted first: a restart that dies on the way still counts.
    merge_params(job.id, {"restarts": restarts + 1})
    reopen_job(job.id)
    claim = _Claim(uid, login_id, GENERATE)
    claim.job_id = job.id
    claim.launch(
        JobQueue(QueueName.MEDIA).put,
        tasks.make_trials,
        len(load(uid, login_id).reels),
        refresh_token=refresh_token(uid) or "",
    )
    return True


@dataclass(frozen=True)
class Trials:
    """One viewer's trials of one login: everything a route of the folder page
    does. `scope`: whose phones the viewer may see (None: everyone's).
    `encode` puts what makes a copy in the line of the media queue (4K encodes,
    one at a time), `enqueue` the rest beside it."""

    uid: int
    scope: int | None
    login_id: int
    encode: Callable[..., object]
    enqueue: Callable[..., object]

    def open(self) -> tuple[DriveCard, FolderView]:
        card, state = _sync_state(self.scope, self.uid, self.login_id)
        return card, FolderView.of(self.uid, state)

    def live(self) -> FolderLive:
        return FolderView.of(self.uid, load(self.uid, self.login_id)).live()

    def view(self) -> FolderView:
        """The folder's view, when the login is one of the viewer's phones' (the
        phones /drive's cards are made from, without building them)."""
        if all(t.login_id != self.login_id for t in drive_store.targets(self.scope)):
            raise NotFoundError(NO_FOLDER)
        return FolderView.of(self.uid, load(self.uid, self.login_id))

    def _claim(self, label: str) -> _Claim:
        return _Claim(self.uid, self.login_id, label)

    def _token(self) -> str:
        return refresh_token(self.uid) or ""

    def _busy(self, job_id: str | None) -> bool:
        return _at_work(self.uid, job_id)

    def _change_reel(self, key: str, edit: Callable[[ReelState], None]) -> FolderState:
        state = change_reel(self.uid, self.login_id, key, edit)
        if state.reel(key) is None:
            raise NotFoundError(NO_REEL)
        return state

    def generate(self) -> Started:
        """POSTED read for new reels: those downloaded and made, a copy already
        made left alone; a second click while it runs does nothing."""
        _sync_state(self.scope, self.uid, self.login_id)
        claim = self._claim(GENERATE)

        def take(state: FolderState) -> None:
            if not self._busy(state.job_id):
                state.job_id = claim.job()
                state.tell(None)
                for reel in state.failed:
                    reel.try_again()

        state = change(self.uid, self.login_id, take)
        if claim.job_id:
            claim.launch(
                self.encode,
                tasks.make_trials,
                len(state.reels),
                refresh_token=self._token(),
            )
        return Started(started=bool(claim.job_id))

    def remake(self, key: str, caption: str | None = None) -> Started:
        """One reel made again as its own job, alongside whatever else runs
        (with `caption` first, when given)."""
        claim = self._claim("Making one trial")

        def take(reel: ReelState) -> None:
            if not self._busy(reel.job_id):
                reel.claim_remake(claim.job(), caption)

        self._change_reel(key, take)
        if claim.job_id:
            claim.launch(
                self.encode,
                tasks.generate_reels,
                1,
                keys=[key],
                refresh_token=self._token(),
            )
        return Started(started=bool(claim.job_id))

    def ask_captions(self, key: str) -> Started:
        """A reel made before there were captions to choose from gets them, as
        its own job (a click on one that has them does nothing: it would cost
        quota)."""
        claim = self._claim("Writing captions")

        def take(reel: ReelState) -> None:
            if not reel.captions and not self._busy(reel.job_id):
                reel.claim_captions(claim.job())

        self._change_reel(key, take)
        if claim.job_id:
            claim.launch(
                self.enqueue,
                tasks.write_captions,
                1,
                key=key,
                refresh_token=self._token(),
            )
        return Started(started=bool(claim.job_id))

    def apply_caption(self, key: str, caption: str) -> Started:
        """A caption chosen or written: saved, then the reel is made again
        with it."""
        text = " ".join(caption.split())[:CAPTION_MAX]
        # An empty box changes nothing: "" would mean "ask the AI", which costs
        # quota.
        if not text:
            return Started(started=False)
        return self.remake(key, text)

    def select(self, key: str, selected: bool) -> Selected:
        self._change_reel(key, lambda r: r.select(selected))
        return Selected(selected=selected)

    def upload(self, approved: list[str] | None) -> Uploading:
        """The selected copies replace what is in TRIALS: each claimed for this
        upload (one being made is left out; one already in Drive can go up
        again, or it would be deleted with the rest). One upload at a time: two
        at once would delete each other's. A copy deleted since (`cleanup`) is
        made again by the upload first, so that one waits its turn to encode."""
        wanted = set(approved or [])
        claim = self._claim("Uploading")
        claimed: list[str] = []

        def take(state: FolderState) -> None:
            if not state.account_folder_id or any(
                r.status == ReelStatus.UPLOADING and self._busy(r.job_id)
                for r in state.reels
            ):
                return
            for reel in state.ready:
                if reel.key in wanted and not self._busy(reel.job_id):
                    reel.claim_upload(claim.job())
                    claimed.append(reel.key)

        state = change(self.uid, self.login_id, take)
        if claim.job_id:
            remade = any(not r.stored for r in state.reels if r.key in claimed)
            claim.launch(
                self.encode if remade else self.enqueue,
                tasks.upload_trials,
                len(claimed),
                refresh_token=self._token(),
                account_folder_id=state.account_folder_id,
                keys=claimed,
            )
        return Uploading(uploading=claimed)

    def _copy(self, key: str) -> Path:
        reel = load(self.uid, self.login_id).reel(key)
        path = at(self.uid, self.login_id, reel.out) if reel else None
        if path is None or not path.is_file():
            raise NotFoundError(NOT_GENERATED)
        return path

    def playable(self, key: str) -> Path:
        """The copy as the page plays it (H.264, `media`); the copy itself when
        no preview can be made."""
        copy = self._copy(key)
        try:
            return media.preview(copy)
        except PREVIEW_ERRORS:
            logger.warning("trials: preview of %s failed", copy, exc_info=True)
            return copy

    def poster(self, key: str) -> Path:
        """The copy's poster; of one deleted since (`cleanup`), the one kept."""
        reel = load(self.uid, self.login_id).reel(key)
        gone = (
            at(self.uid, self.login_id, reel.out) if reel and not reel.stored else None
        )
        if gone is not None and (kept := media.poster_path(gone)).is_file():
            return kept
        copy = self._copy(key)
        try:
            return media.poster(copy)
        except PREVIEW_ERRORS as exc:
            logger.warning("trials: poster of %s failed", copy, exc_info=True)
            raise NotFoundError(NO_POSTER) from exc
