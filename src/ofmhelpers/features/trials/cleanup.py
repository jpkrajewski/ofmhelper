import time
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.jobs.store import get_job
from ofmhelpers.features.previews.render import PREVIEW_ERRORS
from ofmhelpers.features.storage.client import saved_storage
from ofmhelpers.features.trials import folder, media
from ofmhelpers.features.trials.state import FolderState, ReelState, at, change, load
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_BATCH = 20


def _busy(uid: int, job_id: str | None) -> bool:
    if not job_id:
        return False
    job = get_job(job_id, str(uid))
    return job is not None and job.status == "running"


def _old(path: Path, now: float) -> bool:
    try:
        return now - path.stat().st_mtime > settings.trials.keep_s
    except FileNotFoundError:
        logger.debug("trials: %s is gone already", path, exc_info=True)
        return True
    except OSError:
        logger.warning("trials: cannot read %s", path, exc_info=True)
        return True


def _source_files(uid: int, login_id: int, reel: ReelState) -> list[Path]:
    src = at(uid, login_id, reel.local)
    return [] if src is None else [src, src.with_suffix(".ai.mp4")]


def _delete(paths: list[Path]) -> int:
    freed = 0
    for path in paths:
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            logger.debug("trials: %s is gone already", path, exc_info=True)
            continue
        except OSError:
            logger.warning("trials: could not delete %s", path, exc_info=True)
            continue
        freed += size
    return freed


def _due(
    uid: int, login_id: int, state: FolderState, now: float
) -> MappingProxyType[str, int]:
    due: dict[str, int] = {}
    for reel in state.ready:
        copy = at(uid, login_id, reel.out)
        if not reel.stored or copy is None or _busy(uid, reel.job_id):
            continue
        if reel.approved:
            continue
        if reel.posted is not True:
            continue
        if not _old(copy, now):
            continue
        try:
            if copy.is_file():
                media.poster(copy, again=True)
        except PREVIEW_ERRORS:
            logger.warning("trials: poster of %s failed", copy, exc_info=True)
            if not media.poster_path(copy).is_file():
                continue
        due[reel.key] = reel.made
        if len(due) == _BATCH:
            break
    return MappingProxyType(due)


def clean(uid: int, login_id: int, now: float) -> int:
    folder.read_posted(saved_storage(uid).folders, uid, login_id)
    freed = 0
    while True:
        due = _due(uid, login_id, load(uid, login_id), now)
        batch = _clean_batch(uid, login_id, now, due)
        freed += batch.freed
        if len(due) < _BATCH or not batch.cleaned:
            return freed


class _Batch(BaseModel, frozen=True):
    freed: int
    cleaned: int


def _clean_batch(
    uid: int, login_id: int, now: float, due: MappingProxyType[str, int]
) -> _Batch:
    freed = cleaned = 0

    def apply(state: FolderState) -> None:
        nonlocal freed, cleaned
        for reel in state.ready:
            copy = at(uid, login_id, reel.out)
            if (
                copy is None
                or due.get(reel.key) != reel.made
                or _busy(uid, reel.job_id)
            ):
                continue
            freed += _delete(
                [copy, media.preview_path(copy), *_source_files(uid, login_id, reel)]
            )
            reel.cleaned()
            cleaned += 1
        if _busy(uid, state.job_id):
            return
        for reel in state.todo:
            files = _source_files(uid, login_id, reel)
            if files and _old(files[0], now):
                freed += _delete(files)
                reel.source_dropped()

    change(uid, login_id, apply)
    return _Batch(freed=freed, cleaned=cleaned)


def remove_old() -> None:
    now = time.time()
    root = Path(settings.infra.uploads_root) / "trials"
    freed = 0
    for found in root.glob("*/*/state.json"):
        login, user = found.parent, found.parent.parent
        if not (user.name.isdigit() and login.name.isdigit()):
            continue
        try:
            freed += clean(int(user.name), int(login.name), now)
        except Exception:
            logger.warning("trials: cleanup of %s failed", login, exc_info=True)
    if freed:
        logger.info("trials sweep freed %.1f GB", freed / 1e9)
