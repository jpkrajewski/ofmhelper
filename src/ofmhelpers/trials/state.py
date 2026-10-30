"""One viewer x login's trial-reel state: which POSTED reels were pulled,
their caption, the generated copy, approval and upload -- a JSON file in the
folder's work dir. Several writers at once (Generate trials making 28
copies while a caption is applied to one, an upload, the page adding new
POSTED videos): every write is `change()` -- the file read fresh, changed
and replaced under the folder's Redis lock -- so none undoes another's."""

import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, Field
from redis.exceptions import LockError, RedisError

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.config import settings
from ofmhelpers.gdrive.models import folder_url
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

# A write is a read + a small JSON dump: seconds would mean something hangs.
_LOCK_S = 30


class ReelState(BaseModel):
    """One POSTED reel and its trial copy."""

    key: str  # the Drive file id (stable, unique in the folder)
    name: str
    # pending|downloaded|generated|failed|uploading|uploaded
    status: str = "pending"
    caption: str | None = None
    local: str | None = None  # downloaded source, relative to the work dir
    out: str | None = None  # the generated copy, relative to the work dir
    made: int = 0  # times the copy was made: the page's cache buster
    approved: bool = False
    error: str | None = None
    # The job making or uploading this reel now (busy while it runs).
    job_id: str | None = None
    # Its file name in TRIALS once uploaded.
    uploaded_as: str | None = None


class FolderState(BaseModel):
    login_id: int
    account_folder_id: str = ""
    posted_folder_id: str = ""
    trials_folder_id: str | None = None
    # The last Generate trials (one at a time).
    job_id: str | None = None
    # Why the last job could not reach Google Drive (page notice), if so.
    problem: str | None = None
    reels: list[ReelState] = Field(default_factory=list)

    def reel(self, key: str) -> ReelState | None:
        return next((r for r in self.reels if r.key == key), None)

    @property
    def ready(self) -> list[ReelState]:
        """Reels with a generated copy, the page's results."""
        return [r for r in self.reels if r.out]

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
            except (RedisError, LockError):
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
