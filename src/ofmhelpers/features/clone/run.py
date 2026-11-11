"""One /clone run on the worker: the video through Wan 3.0 (`run_ai`), then
its export. Also what recovery re-runs a cut-off clone with, and what a
/clone-links and a long-clone run are made of. `run_ai` and `exported_file`
are looked up here when called: this module is their one seam."""

from pathlib import Path
from typing import Any

from ofmhelpers.features.clone.export import (
    EXPORT_FAILURES,
    export_choice,
    export_results,
    exported_file,
)
from ofmhelpers.features.clone.targets import clone_targets, clone_values
from ofmhelpers.features.generate.service import run_ai
from ofmhelpers.media.exports import ExportOptions
lazy from ofmhelpers.schemas import ResultFile


def run_clone(
    job_id: str,
    api_key: str,
    assets_root: str,
    provider: str,
    prompt: str,
    resolution: str,
    duration: int,
    reference_images: list[str],
    reference_videos: list[str],
    audio: bool = False,
    export: str | None = None,
    mirror: bool = False,
    capcut: bool = False,
    **page_keys: Any,
) -> list[ResultFile]:
    """One clone run. `page_keys` (`model`; older jobs also `clone_batch`,
    `clone_index`) are stored on the job for the page only, but for
    `EXPORT_FAILURES` (earlier runs whose export failed). `capcut` is the
    choice of jobs from before `export` (re-run by recovery)."""
    target = clone_targets()[provider]
    values = clone_values(
        target,
        prompt,
        resolution,
        duration,
        audio,
        reference_images,
        reference_videos[0],
    )
    results = run_ai(
        job_id,
        api_key,
        assets_root,
        provider=provider,
        model=target.model,
        **values.model_dump(),
    )
    return export_results(
        job_id,
        results,
        export_choice(export, capcut),
        ExportOptions(mirror=mirror),
        Path(assets_root),
        failures=int(page_keys.get(EXPORT_FAILURES) or 0),
        exported=exported_file,
    )
