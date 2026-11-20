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
    found: set[Path] = set()
    for named in params_naming(f"/{STAGING}/", {TASK, PREPARE_TASK}):
        for text in _strings(named.params):
            if STAGING in text:
                found.add(Path(text).resolve())
    return found


def _old(path: Path, now: float, age_s: float) -> bool:
    try:
        return now - path.stat().st_mtime > age_s
    except FileNotFoundError:
        logger.debug("edit file %s gone meanwhile", path, exc_info=True)
        return False
    except OSError:
        logger.warning("edit file %s not read", path, exc_info=True)
        return False


def _staging_folders() -> list[Path]:
    root = assets_root_for_actor(None)
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
