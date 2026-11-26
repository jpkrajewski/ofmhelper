# Copyright (C) 2026 jpkrajewski

import time
from pathlib import Path
lazy from collections.abc import Iterator

lazy from pydantic import JsonValue

from ofmhelpers.config import settings
from ofmhelpers.db.jobs.store import files_naming
from ofmhelpers.features.kinds import FileKind
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


def _strings(value: JsonValue) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _named(out: Path) -> set[str]:
    return {
        Path(text).name
        for value in files_naming(out.name)
        for text in _strings(value)
        if out.name in text
    }


def _task_ids() -> list[str]:
    return [
        "".join(c if c.isalnum() or c in "-_" else "_" for c in t.task_id)
        for t in tasks.remembered()
    ]


def stale(now: float) -> list[Path]:
    out = Path(settings.aigen.out_dir)
    keep_s = settings.aigen.raw_keep_s
    try:
        files = [
            p
            for p in out.iterdir()
            if FileKind.from_ext(p.name)
            in (FileKind.IMAGE, FileKind.VIDEO, FileKind.AUDIO)
        ]
    except FileNotFoundError:
        logger.debug("raw-file sweep: %s not made yet", out, exc_info=True)
        return []
    old = []
    for path in files:
        try:
            st = path.stat()
        except FileNotFoundError:
            logger.debug("raw file %s gone meanwhile", path.name, exc_info=True)
            continue
        except OSError:
            logger.warning("raw file %s not read", path.name, exc_info=True)
            continue
        if path.info.is_file() and now - st.st_mtime > keep_s:
            old.append(path)
    if not old:
        return []
    named, task_ids = _named(out), _task_ids()
    return [
        path
        for path in old
        if path.name not in named and not any(t in path.name for t in task_ids)
    ]


def remove_old() -> None:
    removed, freed = 0, 0
    for path in stale(time.time()):
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            logger.warning("raw file %s not removed", path.name, exc_info=True)
            continue
        removed += 1
        freed += size
    if removed:
        logger.info("raw-file sweep removed %d files, %.1f GB", removed, freed / 1e9)
