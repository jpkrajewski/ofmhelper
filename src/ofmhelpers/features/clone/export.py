"""Each generated video is exported as the job's `export`
(`ofmhelpers.media.exports`: original, CapCut, or a phone's camera recording;
default `settings.exports.default`); the exported copy becomes the result and
the provider's hosted original is dropped from it. The provider's raw file is
never the result: an export is tried `settings.exports.attempts` times, and
when every try fails the run fails ("Export failed", user rule 2026-10-25)
keeping its paid request, so recovery runs it again -- the same request
resumed, never paid twice -- up to `settings.exports.reruns` runs
(`EXPORT_FAILURES` on the job counts them)."""

import subprocess
from collections.abc import Callable
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import merge_params
from ofmhelpers.features.assets.store import register_generated_asset
from ofmhelpers.log import get_logger
from ofmhelpers.media.exports import ExportOptions, get_export
from ofmhelpers.schemas import ResultFile

logger = get_logger(__name__)

# A job param: how many of its runs' exports failed every try.
EXPORT_FAILURES = "export_failures"


class ExportFailedError(RuntimeError):
    """Every try of a run's export failed: the run fails with this, never
    with the provider's raw file as its result. `keep_request`: its paid
    request stays remembered (`stores.jobs.resumable`), so recovery's re-run
    exports again without paying -- until `settings.exports.reruns` runs
    failed so."""

    def __init__(self, export: str) -> None:
        super().__init__(f"Export failed ({export}): run it again")
        self.keep_request = True


def export_choice(export: str | None, capcut: bool = False) -> str:
    """The export a job asked for; jobs from before `export` stored only
    the `capcut` bool."""
    return export or ("capcut" if capcut else "original")


def exported_file(
    result: ResultFile, export: str, options: ExportOptions, assets_root: Path
) -> ResultFile:
    """`result` exported as `export` with `options`, next to the original and registered
    as a reusable asset like it. A remote-only result (the local download
    failed) has nothing to export and is kept as it is. The export is tried
    `settings.exports.attempts` times; every try failing raises
    `ExportFailedError` -- the provider's raw file is never the result."""
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


def export_results(
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
    """Every result exported (`exported`). An export that failed every try
    is counted on the job (`failures`: earlier runs'), its paid request kept
    for recovery's re-run while fewer than `settings.exports.reruns` runs
    failed so; then raised."""
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
