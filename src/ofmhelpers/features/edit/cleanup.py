"""The /edit sweep (features/jobs/sweeps.py, hourly): what /edit makes is
kept a day, unless a job of another tool uses it.

Each round deletes, older than `edit_keep_s` (a day): every file in a
`.edit/` folder of the store (the agency's and each person's: uploads and
cuts), unless a job names it in its params -- a job of another tool whatever
its status, for as long as the job is there (a clone of a cut sends it when
it runs and gives it back on Recreate -- prod 2026-11-07: kept only while
running, the cut of a clone made the day before was gone, Recreate said
"... is not on this server"), /edit's own cut or light copy only while it
runs or waits in the edit line (the upload it reads); every light copy and frame strip
the page plays and shows (`prepared_dir`, and the strips made before it).
And every cut's
work folder (`uploads/.edit/tmp*`) older than twice a cut's time limit: a
worker killed mid-cut leaves one.
"""

import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import params_naming
from ofmhelpers.features.assets.store import assets_root_for_actor
from ofmhelpers.features.edit.service import (
    PREPARE_TASK,
    STAGING,
    TASK,
    prepared_dir,
)
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
    """Every `.edit/` path a job's params name; /edit's own jobs only while
    they run or wait (a cut or a light copy done names the video it was made
    from: that one still goes after its day)."""
    found: set[Path] = set()
    for named in params_naming(f"/{STAGING}/", {TASK, PREPARE_TASK}):
        for text in _strings(named.params):
            if STAGING in text:
                found.add(Path(text).resolve())
    return found


def _old(path: Path, now: float, age_s: float) -> bool:
    try:
        return now - path.stat().st_mtime > age_s
    except OSError:
        return False


def _staging_folders() -> list[Path]:
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
    for folder in (prepared_dir(), previews_dir() / "strips"):
        for path in folder.glob("*"):
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
