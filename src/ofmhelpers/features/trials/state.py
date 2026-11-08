"""One viewer x login's trial-reel state: which POSTED reels were pulled,
their caption, the generated copy, approval and upload -- a JSON file in the
folder's work dir. Several writers at once (Generate trials making 28
copies while a caption is applied to one, an upload, the page adding new
POSTED videos): every write is `change()` -- the file read fresh, changed
and replaced under the folder's Redis lock -- so none undoes another's."""

import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import LockError, RedisError

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.config import settings
from ofmhelpers.integrations.gdrive.models import folder_url
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

# A write is a read + a small JSON dump: seconds would mean something hangs.
_LOCK_S = 30

CAPTION_MAX = 80
# A reel with no POSTED date sorts after every one that has it.
_NEVER = datetime.min.replace(tzinfo=UTC)


class ReelStatus(StrEnum):
    """A reel's step, stored as this text in state.json."""

    PENDING = "pending"
    DOWNLOADED = "downloaded"
    GENERATED = "generated"
    FAILED = "failed"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"


class ReelState(BaseModel):
    """One POSTED reel and its trial copy."""

    # A status assigned as plain text still becomes a ReelStatus.
    model_config = ConfigDict(validate_assignment=True)

    key: str  # the Drive file id (stable, unique in the folder)
    name: str
    # Its createdTime in POSTED: the page shows the newest first.
    posted_at: datetime | None = None
    status: ReelStatus = ReelStatus.PENDING
    caption: str | None = None
    # What the AI wrote to choose from (empty: a reel made before there were
    # choices); `caption` is the one on the copy, maybe the person's own.
    captions: list[str] = Field(default_factory=list)
    local: str | None = None  # downloaded source, relative to the work dir
    out: str | None = None  # the generated copy, relative to the work dir
    made: int = 0  # times the copy was made: the page's cache buster
    approved: bool = False
    error: str | None = None
    # The job making or uploading this reel now (busy while it runs).
    job_id: str | None = None
    # Its file name in TRIALS once uploaded.
    uploaded_as: str | None = None

    def posted_or_never(self) -> datetime:
        return self.posted_at or _NEVER

    def claim_remake(self, job_id: str, caption: str | None = None) -> None:
        """Taken by a job making it again (with `caption`, when given)."""
        self.job_id, self.error = job_id, None
        if caption is not None:
            self.caption = caption

    def claim_upload(self, job_id: str) -> None:
        self.job_id, self.status, self.error = job_id, ReelStatus.UPLOADING, None
        self.approved = False

    def claim_captions(self, job_id: str) -> None:
        self.job_id, self.error = job_id, None

    def select(self, selected: bool) -> None:
        self.approved = selected

    def mark_downloaded(self, local: str) -> None:
        self.local, self.status, self.error = local, ReelStatus.DOWNLOADED, None

    def mark_made(self, caption: str, out: str, captions: list[str]) -> None:
        """Its new copy is not the one in Drive, if one is."""
        self.caption, self.out, self.made = caption, out, self.made + 1
        self.captions = captions
        self.status, self.error = ReelStatus.GENERATED, None
        self.approved, self.job_id, self.uploaded_as = False, None, None

    def mark_failed(self, error: str) -> None:
        self.status, self.error, self.job_id = ReelStatus.FAILED, error, None

    def try_again(self) -> None:
        """A new Generate takes it again: no longer counted as failed."""
        self.status = ReelStatus.DOWNLOADED if self.local else ReelStatus.PENDING
        self.error = None

    def mark_captions(self, captions: list[str]) -> None:
        self.captions, self.job_id, self.error = captions, None, None

    def captions_failed(self, error: str) -> None:
        self.job_id, self.error = None, error

    def mark_uploaded(self, name: str) -> None:
        self.status, self.uploaded_as = ReelStatus.UPLOADED, name
        self.job_id, self.error = None, None

    def left_drive(self) -> None:
        """Replaced by a newer batch: selectable again."""
        self.uploaded_as = None
        if self.status == ReelStatus.UPLOADED:
            self.status = ReelStatus.GENERATED

    def upload_failed(self, error: str) -> None:
        """Its batch did not go up: as it was before the click, still selected,
        saying why."""
        was_in_drive = self.uploaded_as is not None
        self.status = ReelStatus.UPLOADED if was_in_drive else ReelStatus.GENERATED
        self.job_id, self.error, self.approved = None, error, True


class FolderState(BaseModel):
    login_id: int
    account_folder_id: str = ""
    posted_folder_id: str = ""
    trials_folder_id: str | None = None
    # The last Generate trials (one at a time).
    job_id: str | None = None
    # Why the last job could not reach Google Drive (page notice), if so.
    problem: str | None = None
    # What the last Generate trials left (status line): nothing new in POSTED,
    # or how many it could not make.
    note: str | None = None
    # Newest in POSTED first (`tasks.merge_posted`): the page's order.
    reels: list[ReelState] = Field(default_factory=list)

    def reel(self, key: str) -> ReelState | None:
        return next((r for r in self.reels if r.key == key), None)

    def report(self, problem: str | None) -> None:
        self.problem = problem

    def tell(self, note: str | None) -> None:
        self.note = note

    def remember_trials_folder(self, folder_id: str) -> None:
        self.trials_folder_id = folder_id

    @property
    def ready(self) -> list[ReelState]:
        """Reels with a generated copy, the page's results."""
        return [r for r in self.reels if r.out]

    @property
    def failed(self) -> list[ReelState]:
        """Reels with no copy that the last Generate could not make."""
        return [r for r in self.todo if r.status == ReelStatus.FAILED]

    @property
    def trials_url(self) -> str | None:
        return folder_url(self.trials_folder_id) if self.trials_folder_id else None

    @property
    def todo(self) -> list[ReelState]:
        """Reels with no copy yet: what Generate trials makes."""
        return [r for r in self.reels if not r.out]


def work_dir(user_id: int, login_id: int) -> Path:
    """Read per call: the uploads root is a setting."""
    root = Path(settings.infra.uploads_root) / "trials"
    return root / str(user_id) / str(login_id)


def load(user_id: int, login_id: int) -> FolderState:
    path = work_dir(user_id, login_id) / "state.json"
    if path.is_file():
        return FolderState.model_validate_json(path.read_text(encoding="utf-8"))
    return FolderState(login_id=login_id)


def save(user_id: int, state: FolderState) -> None:
    root = work_dir(user_id, state.login_id)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=root, delete=False, prefix="state_", suffix=".tmp"
    ) as handle:
        handle.write(state.model_dump_json(indent=1))
        tmp = Path(handle.name)
    tmp.replace(root / "state.json")


@contextmanager
def locked(
    user_id: int, login_id: int, what: str = "state", wait_s: int = _LOCK_S
) -> Iterator[None]:
    """One of the folder's locks (`what`) across the app and every worker.
    Redis down or the wait over: logged, and the work goes ahead unlocked."""
    lock = get_redis().lock(
        f"lock:trials:{what}:{user_id}:{login_id}",
        timeout=wait_s,
        blocking_timeout=wait_s,
    )
    try:
        held = bool(lock.acquire())
    except RedisError:
        logger.warning("trials: state lock unavailable", exc_info=True)
        held = False
    try:
        yield
    finally:
        if held:
            try:
                lock.release()
            except RedisError, LockError:
                logger.warning(
                    "trials: state lock of %s/%s not released", user_id, login_id
                )


def change(
    user_id: int, login_id: int, edit: Callable[[FolderState], None]
) -> FolderState:
    """`edit` applied to the state as it is now, saved; the saved state."""
    with locked(user_id, login_id):
        state = load(user_id, login_id)
        edit(state)
        save(user_id, state)
    return state


def change_reel(
    user_id: int, login_id: int, key: str, edit: Callable[[ReelState], None]
) -> FolderState:
    """`edit` applied to one reel as it is now (gone meanwhile: nothing)."""

    def apply(state: FolderState) -> None:
        if (reel := state.reel(key)) is not None:
            edit(reel)

    return change(user_id, login_id, apply)


def at(user_id: int, login_id: int, rel: str | None) -> Path | None:
    """A state's relative path as an absolute one inside the work dir."""
    if rel is None:
        return None
    root = work_dir(user_id, login_id).resolve()
    path = (root / rel).resolve()
    if not str(path).startswith(str(root)):
        msg = f"outside the trials work dir: {rel!r}"
        raise ValueError(msg)
    return path
