# Copyright (C) 2026 jpkrajewski

import shutil
import time
lazy from collections.abc import Callable
lazy from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.features.assets.store import uploads_root
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

FOLDER = "viral"


def _newest_change(folder: Path) -> float:
    return max((p.stat().st_mtime for p in (folder, *folder.rglob("*"))), default=0.0)


def _old(folder: Path, now: float) -> bool:
    try:
        return now - _newest_change(folder) > settings.viral.keep_s
    except FileNotFoundError:
        logger.debug("viral: %s is gone already", folder, exc_info=True)
        return False
    except OSError:
        logger.warning("viral: cannot read %s", folder, exc_info=True)
        return False


def _not_removed(step: Callable[..., object], path: str, error: BaseException) -> None:
    logger.warning("viral: %s not removed (%s)", path, step.__name__, exc_info=error)


def remove_old() -> int:
    root = uploads_root() / FOLDER
    now = time.time()
    removed = 0
    for folder in root.glob("*"):
        if folder.is_dir() and _old(folder, now):
            shutil.rmtree(folder, onexc=_not_removed)
            if not folder.exists():
                removed += 1
    if removed:
        logger.info("viral sweep removed %s search folders", removed)
    return removed
