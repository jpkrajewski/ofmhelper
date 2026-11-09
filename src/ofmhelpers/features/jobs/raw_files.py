"""The raw-file sweep (features/jobs/sweeps.py, daily). A provider's download
(`{provider}_{task}.mp4` in `settings.aigen.out_dir`) is exported, and the
export becomes the job's result. Nothing reads the raw file again once the
job's request can no longer be resumed (`resume_max_age_s`, 48 h). Kept
forever, the raw files took 7 GB of prod's 72 GB disk, 3.5 GB of it older than
14 days (2026-11-08, disk at 93%).

Each round deletes a picture, video or sound file at the top of `out_dir` when
it is older than `raw_keep_s` (14 days) and nothing names it:
- no job's result or params, of any status (a /generate image is its own
  result, an export is its job's);
- no remembered provider task (`tasks.remembered()`: recovery may still
  resume it).

Folders (`fitted/` 14 MB, `higgsfield/` 2 MB on prod) and other files (the
`.jsonl` logs) are never touched. Nor is the copy the asset store keeps of each
download (`uploads/assets`, the Reuse picker's): a full copy, not a hardlink,
since `uploads` and `kieai_out` are two mounts (prod 2026-11-08: link count 1).
"""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import files_naming
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_MEDIA = {"image", "video", "audio"}


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _named(out: Path) -> set[str]:
    """The file names every job's result or params names under `out`'s folder
    name (wherever that folder is mounted)."""
    return {
        Path(text).name
        for value in files_naming(out.name)
        for text in _strings(value)
        if out.name in text
    }


def _task_ids() -> list[str]:
    """Remembered provider tasks, as `runner._result_path` writes them in a
    file name."""
    return [
        "".join(c if c.isalnum() or c in "-_" else "_" for c in t.task_id)
        for t in tasks.remembered()
    ]


def stale(now: float) -> list[Path]:
    """The raw files a round deletes at `now`."""
    out = Path(settings.aigen.out_dir)
    keep_s = settings.aigen.raw_keep_s
    try:
        files = [p for p in out.iterdir() if classify_kind(p.name) in _MEDIA]
    except OSError:
        return []
    old = []
    for path in files:
        try:
            st = path.stat()
        except OSError:
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
