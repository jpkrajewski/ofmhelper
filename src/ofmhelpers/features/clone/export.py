# Copyright (C) 2026 jpkrajewski

import logging
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError
lazy from collections.abc import Callable

from ofmhelpers.config import settings
from ofmhelpers.db.jobs.schema import ResultFile
from ofmhelpers.db.jobs.store import merge_params
from ofmhelpers.features.assets.store import register_generated_asset
from ofmhelpers.features.refusals import ResumableError
from ofmhelpers.media.exports import ExportOptions, get_export

logger = logging.getLogger(__name__)

EXPORT_FAILURES = "export_failures"


class ExportFailedError(ResumableError):
    def __init__(self, export: str) -> None:
        super().__init__(f"Export failed ({export}): run it again")


def export_choice(export: str | None, capcut: bool = False) -> str:  # noqa: FBT001, FBT002
    return export or ("capcut" if capcut else "original")


def exported_file(
    result: ResultFile,
    export: str,
    options: ExportOptions,
    assets_root: Path,
) -> ResultFile:
    if result.path is None:
        return result
    src = Path(result.path)
    tries = settings.exports.attempts
    for attempt in range(1, tries + 1):
        try:
            dst = get_export(export).render(src, options)
            break
        except (
            OSError,
            RuntimeError,
            ValueError,
            ZoneInfoNotFoundError,
            subprocess.SubprocessError,
        ):
            logger.warning(
                "%s export of %s failed (try %d of %d)",
                export,
                src.name,
                attempt,
                tries,
                exc_info=True,
            )
    else:
        raise ExportFailedError(export)
    if dst == src:
        return result
    register_generated_asset(dst, assets_root)
    return ResultFile(name=dst.name, path=str(dst), remote_url=None)


def export_results(  # noqa: PLR0913
    job_id: str,
    results: list[ResultFile],
    export: str,
    options: ExportOptions,
    assets_root: Path,
    *,
    failures: int,
    exported: Callable[[ResultFile, str, ExportOptions, Path], ResultFile] = (
        exported_file
    ),
) -> list[ResultFile]:
    try:
        return [exported(r, export, options, assets_root) for r in results]
    except ExportFailedError as exc:
        failures += 1
        merge_params(job_id, {EXPORT_FAILURES: failures})
        exc.keep_request = failures < settings.exports.reruns
        logger.exception(
            "job %s: %s export failed (run %d of %d)",
            job_id,
            export,
            failures,
            settings.exports.reruns,
        )
        raise
