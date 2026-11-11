"""The response shapes every status/polling page reuses.

None of these touch disk beyond a stat: they turn a job into what a
template or the polling JS expects, so every tool's status endpoint is one
line rather than its own bespoke payload.
"""

from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from ofmhelpers.features.assets import cards
from ofmhelpers.features.assets.cards import asset_card
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.store import strip_asset_hash_prefix
from ofmhelpers.schemas import (
    AssetCard,
    FailedSource,
    InputFileGroup,
    InputSetting,
    Job,
    JobInputs,
    JobStatusPayload,
    ReferenceAsset,
)

__all__ = [
    "asset_card",
    "job_assets",
    "job_inputs",
    "job_status_payload",
    "reference_asset",
]


def job_assets(
    job: Job, files_prefix: str
) -> tuple[list[AssetCard], list[FailedSource]]:
    """`cards.job_assets` as the two lists the routers unpack."""
    found = cards.job_assets(job, files_prefix)
    return found.assets, found.failed_sources


def reference_asset(path: str) -> ReferenceAsset:
    """One reference-file entry for a job's "inputs" display: a file already
    sitting in the shared uploads/assets/ store. Only ever rendered small
    (see .inputs-refs .result-item CSS, ~200px), so images go through
    /refs/thumb (cached, small) rather than /refs/file (full original) --
    this dict has no other consumer that needs the full-res URL."""
    name = strip_asset_hash_prefix(Path(path).name)
    kind = classify_kind(name)
    quoted = quote(path)
    view_url = (
        f"/refs/thumb?path={quoted}&size=200"
        if kind == "image"
        else f"/refs/file?path={quoted}"
    )
    return ReferenceAsset(name=name, kind=kind, view_url=view_url)


# Params long enough that they belong in their own block (below the settings
# table) rather than crammed into a table cell -- currently just the prompt,
# but keyed generically in case a future field needs the same treatment.
_LONG_TEXT_THRESHOLD = 60


def job_inputs(job: Job) -> JobInputs:
    """Splits a job's stored params into the three shapes the "Inputs"
    section on an AI-generation job-status page renders: short scalar
    settings (a table), long text (prompt -- its own block), and reference
    file lists (previewed via reference_asset). Generic over every AI-gen
    task's params shape -- a param is a reference-file list simply because
    its value is a list at all; none of these jobs ever store a list of
    anything else."""
    inputs = JobInputs(settings=[], long_text=[], file_groups=[])
    for key, value in job.params.items():
        label = key.replace("_", " ")
        if isinstance(value, list):
            if value:  # an empty ref slot has nothing worth showing
                inputs.file_groups.append(
                    InputFileGroup(
                        label=label, assets=[reference_asset(p) for p in value]
                    )
                )
        elif isinstance(value, str) and len(value) > _LONG_TEXT_THRESHOLD:
            inputs.long_text.append(InputSetting(label=label, value=value))
        else:
            inputs.settings.append(InputSetting(label=label, value=value))
    return inputs


def job_status_payload(job: Job | None, files_prefix: str) -> JobStatusPayload:
    """JSON body for the `/{prefix}/jobs/{job_id}/status` polling endpoint used
    by the inline (no-redirect) generation UI, and by the /generate gallery's
    click-to-reuse feature (task/params)."""
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    assets, failed_sources = job_assets(job, files_prefix)
    return JobStatusPayload(
        job_id=job.id,
        task=job.task,
        params=job.params,
        status=job.status,
        error=job.error,
        result=assets,
        # Grouped download jobs can finish "done" with some URLs errored.
        failed_sources=failed_sources,
        # A generation still in flight can already have a hosted result URL
        # (kie.ai's poll succeeded; only the local download is still
        # running) -- surface it so the frontend can render it immediately
        # instead of blocking on the download. Only ever read while
        # "running"; once the job finishes the real result takes over.
        preview=job.preview if job.status == "running" else None,
    )
