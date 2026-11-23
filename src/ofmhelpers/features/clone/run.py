# Copyright (C) 2026 jpkrajewski

from pathlib import Path

from ofmhelpers.features.clone.export import (
    export_choice,
    export_results,
    exported_file,
)
from ofmhelpers.features.clone_pricing.targets import clone_targets, clone_values
from ofmhelpers.features.generate.service import run_ai
from ofmhelpers.media.exports import ExportOptions
lazy from ofmhelpers.db.jobs.schema import ResultFile


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
    export_failures: int = 0,
    model: str | None = None,  # noqa: ARG001
    link: str | None = None,  # noqa: ARG001
    batch: str | None = None,  # noqa: ARG001
) -> list[ResultFile]:
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
        failures=export_failures,
        exported=exported_file,
    )
