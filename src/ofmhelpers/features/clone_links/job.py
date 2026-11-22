from ofmhelpers.features.clone.run import run_clone
from ofmhelpers.features.clone_links import run as clone_link_run
lazy from ofmhelpers.db.jobs.schema import ResultFile


def run_clone_link(
    job_id: str,
    api_key: str,
    assets_root: str,
    link: str,
    provider: str,
    prompt: str,
    resolution: str,
    reference_images: list[str],
    audio: bool = False,
    export: str | None = None,
    mirror: bool = False,
    capcut: bool = False,
    model: str | None = None,
    batch: str | None = None,
    source_video: str | None = None,
    source_seconds: float | None = None,
    export_failures: int = 0,
    reference_videos: list[str] | None = None,
    duration: int | None = None,
) -> list[ResultFile]:
    return clone_link_run.run_clone_link(
        job_id,
        api_key,
        assets_root,
        link,
        provider,
        prompt,
        resolution,
        reference_images,
        audio,
        export,
        mirror,
        capcut,
        model,
        batch,
        source_video,
        source_seconds,
        export_failures,
        reference_videos,
        duration,
        run_clone=run_clone,
    )
