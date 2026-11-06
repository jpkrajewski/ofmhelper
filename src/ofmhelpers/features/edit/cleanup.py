"""The /edit sweep (features/jobs/sweeps.py, hourly): nothing /edit makes
is kept.

Each round deletes, older than `edit_keep_s` (a day): every file in a
`.edit/` folder of the store (the agency's and each person's: uploads and
cuts), unless a running job's params name it (a clone queued with a cut
reads it when it sends the request); every frame strip. And every cut's
work folder (`uploads/.edit/tmp*`) older than twice a cut's time limit: a
worker killed mid-cut leaves one.
"""

import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import running_jobs
from ofmhelpers.features.edit.service import STAGING
from ofmhelpers.features.previews.render import previews_dir
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _in_use() -> set[Path]:
    """Every path a running (or queued) job's params name."""
    found: set[Path] = set()
    for job in running_jobs():
        for text in _strings(job.params):
            if STAGING in text:
                found.add(Path(text).resolve())
    return found


def _old(path: Path, now: float, age_s: float) -> bool:
    try:
        return now - path.stat().st_mtime > age_s
    except OSError:
        return False


def _staging_folders() -> list[Path]:
    from ofmhelpers.web.routers.task_helpers import assets_root_for_actor

    root = assets_root_for_actor(None)  # the store root, read at call time
    return [root / STAGING, *root.glob(f"users/*/{STAGING}")]


def remove_old() -> None:
    web = settings.web
    now = time.time()
    in_use = _in_use()
    removed = 0
    for folder in _staging_folders():
        for path in folder.glob("*"):
            if (
                path.is_file()
                and _old(path, now, web.edit_keep_s)
                and path.resolve() not in in_use
            ):
                path.unlink(missing_ok=True)
                removed += 1
    for path in (previews_dir() / "strips").glob("*"):
        if path.is_file() and _old(path, now, web.edit_keep_s):
            path.unlink(missing_ok=True)
            removed += 1
    work = Path(settings.infra.uploads_root) / STAGING
    for path in work.glob("tmp*"):
        if path.is_dir() and _old(path, now, 2 * web.edit_cut_timeout_s):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    if removed:
        logger.info("edit sweep removed %s old files", removed)
