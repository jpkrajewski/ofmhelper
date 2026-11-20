from ofmhelpers.features.assets.store import assets_root_for_actor
from ofmhelpers.features.downloads import images as download_images
from ofmhelpers.features.downloads import media as download_media
from ofmhelpers.features.downloads import videos as download_videos
from ofmhelpers.features.jobs.restart import Restart, RunArgs
lazy from ofmhelpers.db.jobs.schema import Job


def _download(job: Job) -> RunArgs:
    actor = job.actor
    return RunArgs.model_validate(
        {
            "urls": job.params["urls"],
            "assets_root": str(assets_root_for_actor(actor)),
            "user_id": int(actor) if actor and actor.isdigit() else None,
            "job_id": job.id,
        }
    )


RESTARTS = frozendict(
    {
        download_media.TASK: Restart(
            run=download_media.run_downloads, kwargs=_download
        ),
        download_videos.TASK: Restart(
            run=download_videos.run_downloads, kwargs=_download
        ),
        download_images.TASK: Restart(
            run=download_images.run_downloads, kwargs=_download
        ),
    }
)
