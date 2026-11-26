# Copyright (C) 2026 jpkrajewski

from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from ofmhelpers.db.jobs.schema import Job
from ofmhelpers.features.assets.cards import asset_card, job_assets
from ofmhelpers.features.assets.store import strip_asset_hash_prefix
from ofmhelpers.features.jobs.models import JobStatusPayload
from ofmhelpers.features.jobs.schema import (
    InputFileGroup,
    InputSetting,
    JobInputs,
    ReferenceAsset,
)
from ofmhelpers.features.jobs.stage import stage_of
from ofmhelpers.features.kinds import FileKind

__all__ = [
    "asset_card",
    "job_assets",
    "job_inputs",
    "job_status_payload",
    "reference_asset",
]


def reference_asset(path: str) -> ReferenceAsset:
    name = strip_asset_hash_prefix(Path(path).name)
    kind = FileKind.from_ext(name)
    quoted = quote(path)
    view_url = (
        f"/refs/thumb?path={quoted}&size=200"
        if kind == FileKind.IMAGE
        else f"/refs/file?path={quoted}"
    )
    return ReferenceAsset(name=name, kind=kind, view_url=view_url)


_LONG_TEXT_THRESHOLD = 60


def job_inputs(job: Job) -> JobInputs:
    inputs = JobInputs(settings=[], long_text=[], file_groups=[])
    for key, value in job.params.items():
        label = key.replace("_", " ")
        if isinstance(value, list):
            if value:
                inputs.file_groups.append(
                    InputFileGroup(
                        label=label,
                        assets=[reference_asset(p) for p in value],
                    ),
                )
        elif isinstance(value, str) and len(value) > _LONG_TEXT_THRESHOLD:
            inputs.long_text.append(InputSetting(label=label, value=value))
        else:
            inputs.settings.append(InputSetting(label=label, value=value))
    return inputs


def job_status_payload(job: Job | None, files_prefix: str) -> JobStatusPayload:
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    found = job_assets(job, files_prefix)
    return JobStatusPayload(
        job_id=job.id,
        task=job.task,
        params=job.params,
        status=job.status,
        error=job.error,
        result=found.assets,
        failed_sources=found.failed_sources,
        preview=job.preview if job.status == "running" else None,
        stage=stage_of(job),
    )
