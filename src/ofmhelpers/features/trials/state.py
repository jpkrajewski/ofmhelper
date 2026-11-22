import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import LockError, RedisError

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.drive_folders.schema import folder_url
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = get_logger(__name__)

_LOCK_S = 30

CAPTION_MAX = 80
_NEVER = datetime.min.replace(tzinfo=UTC)


class ReelStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    GENERATED = "generated"
    FAILED = "failed"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"


class Recipe(BaseModel):
    seed: int
    caption: str
    pipeline: str = ""
    draws: dict[str, Any] | None = None


class ReelState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    key: str
    name: str
    posted_at: datetime | None = None
    status: ReelStatus = ReelStatus.PENDING
    caption: str | None = None
    captions: list[str] = Field(default_factory=list)
    local: str | None = None
    out: str | None = None
    made: int = 0
    made_at: datetime | None = None
    approved: bool = False
    error: str | None = None
    job_id: str | None = None
    uploaded_as: str | None = None
    recipe: Recipe | None = None
    posted: bool | None = None
    stored: bool = True

    def posted_or_never(self) -> datetime:
        return self.posted_at or _NEVER

    def made_or_posted(self) -> datetime:
        return self.made_at or self.posted_or_never()

    def claim_remake(self, job_id: str, caption: str | None = None) -> None:
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

    def source_back(self, local: str) -> None:
        self.local = local

    def mark_made(self, out: str, captions: list[str], recipe: Recipe) -> None:
        self.caption, self.out, self.made = recipe.caption, out, self.made + 1
        self.made_at = datetime.now(UTC)
        self.captions, self.recipe, self.stored = captions, recipe, True
        self.status, self.error = ReelStatus.GENERATED, None
        self.approved, self.job_id, self.uploaded_as = False, None, None

    def restored(self) -> None:
        self.stored = True

    def cleaned(self) -> None:
        self.stored, self.local = False, None

    def source_dropped(self) -> None:
        self.local = None
        if self.status == ReelStatus.DOWNLOADED:
            self.status = ReelStatus.PENDING

    def mark_failed(self, error: str) -> None:
        self.status, self.error, self.job_id = ReelStatus.FAILED, error, None

    def try_again(self) -> None:
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
        self.uploaded_as = None
        if self.status == ReelStatus.UPLOADED:
            self.status = ReelStatus.GENERATED

    def upload_failed(self, error: str) -> None:
        was_in_drive = self.uploaded_as is not None
        self.status = ReelStatus.UPLOADED if was_in_drive else ReelStatus.GENERATED
        self.job_id, self.error, self.approved = None, error, True


class FolderState(BaseModel):
    login_id: int
    account_folder_id: str = ""
    posted_folder_id: str = ""
    trials_folder_id: str | None = None
    job_id: str | None = None
    problem: str | None = None
    note: str | None = None
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
        return [r for r in self.reels if r.out]

    @property
    def failed(self) -> list[ReelState]:
        return [r for r in self.todo if r.status == ReelStatus.FAILED]

    @property
    def trials_url(self) -> str | None:
        return folder_url(self.trials_folder_id) if self.trials_folder_id else None

    @property
    def todo(self) -> list[ReelState]:
        return [r for r in self.reels if not r.out]


def work_dir(user_id: int, login_id: int) -> Path:
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
                    "trials: state lock of %s/%s not released",
                    user_id,
                    login_id,
                    exc_info=True,
                )


def change(
    user_id: int, login_id: int, edit: Callable[[FolderState], None]
) -> FolderState:
    with locked(user_id, login_id):
        state = load(user_id, login_id)
        edit(state)
        save(user_id, state)
    return state


def change_reel(
    user_id: int, login_id: int, key: str, edit: Callable[[ReelState], None]
) -> FolderState:

    def apply(state: FolderState) -> None:
        if (reel := state.reel(key)) is not None:
            edit(reel)

    return change(user_id, login_id, apply)


def at(user_id: int, login_id: int, rel: str | None) -> Path | None:
    if rel is None:
        return None
    root = work_dir(user_id, login_id).resolve()
    path = (root / rel).resolve()
    if not str(path).startswith(str(root)):
        msg = f"outside the trials work dir: {rel!r}"
        raise ValueError(msg)
    return path
