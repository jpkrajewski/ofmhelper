"""The trials sweep (features/jobs/sweeps.py, hourly). A person makes many
copies and uploads a few that day; kept forever, each reel's 4K copy, its page
preview and its source took 13 GB of prod's 72 GB disk (922 copies,
2026-11-11, disk at 90%).

A copy older than `keep_s` (a day) loses its video, preview and source; its
poster (a frame from the middle) and its recipe in state.json stay, a few KB.
Nothing is lost by it: the source is still in Drive under the reel's key, and
the recipe (seed + caption) makes the same edit again -- `tasks.Folder.make` does
when the reel is uploaded, regenerated or given a caption. A reel a job is
working on is left alone, and so is one whose poster cannot be made, and one
whose source POSTED no longer has (`ReelState.posted`, read again first): that
copy could never be made again.
"""

import time
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.features.previews.render import PREVIEW_ERRORS
from ofmhelpers.features.trials import folder, media
from ofmhelpers.features.trials.state import FolderState, ReelState, at, change, load
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

# Copies of one folder cleaned per write: a round cut off (a deploy, the job's
# time limit) has lost the posters of at most this many, not of the folder.
_BATCH = 20


def _busy(uid: int, job_id: str | None) -> bool:
    if not job_id:
        return False
    job = get_job(job_id, str(uid))
    return job is not None and job.status == "running"


def _old(path: Path, now: float) -> bool:
    try:
        return now - path.stat().st_mtime > settings.trials.keep_s
    except OSError:
        return True  # gone already: nothing to keep


def _source_files(uid: int, login_id: int, reel: ReelState) -> list[Path]:
    src = at(uid, login_id, reel.local)
    return [] if src is None else [src, src.with_suffix(".ai.mp4")]


def _delete(paths: list[Path]) -> int:
    """Bytes freed."""
    freed = 0
    for path in paths:
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        freed += size
    return freed


def _due(uid: int, login_id: int, state: FolderState, now: float) -> dict[str, int]:
    """Reels whose copy is a day old, by key, with the make it is of; each
    with its middle poster made now, while the video is still there."""
    due: dict[str, int] = {}
    for reel in state.ready:
        copy = at(uid, login_id, reel.out)
        if not reel.stored or copy is None or _busy(uid, reel.job_id):
            continue
        # Its source is not known to be in Drive: the copy is all there is.
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
                continue  # nothing would be left to show: the video stays
        due[reel.key] = reel.made
        if len(due) == _BATCH:
            break
    return due


def clean(uid: int, login_id: int, now: float) -> int:
    """One folder, a batch of copies at a time; bytes freed."""
    folder.read_posted(uid, login_id)
    freed = 0
    while True:
        due = _due(uid, login_id, load(uid, login_id), now)
        got, cleaned = _clean_batch(uid, login_id, now, due)
        freed += got
        # None cleaned: what is left is taken by a job, or has no poster.
        if len(due) < _BATCH or not cleaned:
            return freed


def _clean_batch(
    uid: int, login_id: int, now: float, due: dict[str, int]
) -> tuple[int, int]:
    """(bytes freed, copies cleaned)."""
    freed = cleaned = 0

    def apply(state: FolderState) -> None:
        nonlocal freed, cleaned
        for reel in state.ready:
            copy = at(uid, login_id, reel.out)
            # Made again, or taken by a job, since it was looked at: not now.
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
            return  # Generate is downloading and making what has no copy yet
        for reel in state.todo:
            files = _source_files(uid, login_id, reel)
            if files and _old(files[0], now):
                freed += _delete(files)
                reel.source_dropped()

    change(uid, login_id, apply)
    return freed, cleaned


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
        except Exception:  # one folder must not stop the others
            logger.warning("trials: cleanup of %s failed", login, exc_info=True)
    if freed:
        logger.info("trials sweep freed %.1f GB", freed / 1e9)
