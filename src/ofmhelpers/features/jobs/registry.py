from collections.abc import Callable
from typing import Any

from ofmhelpers.cache import QueueName
from ofmhelpers.config import settings
from ofmhelpers.features.assets.store import assets_root_for_actor
from ofmhelpers.features.character_sheet import service as character_sheet
from ofmhelpers.features.clone import service as clone
from ofmhelpers.features.clone.run import run_clone
from ofmhelpers.features.clone_links import service as clone_links
from ofmhelpers.features.clone_links.job import run_clone_link
from ofmhelpers.features.clone_long import page as clone_long_page
from ofmhelpers.features.clone_long import run_long_clone
from ofmhelpers.features.clone_long import time_limit as clone_long_time_limit
from ofmhelpers.features.downloads import images as download_images
from ofmhelpers.features.downloads import media as download_media
from ofmhelpers.features.downloads import videos as download_videos
from ofmhelpers.features.edit import service as edit_service
from ofmhelpers.features.generate import service as generate
from ofmhelpers.features.handmade import service as handmade
from ofmhelpers.features.jobs.restart import COUNT, Restart
from ofmhelpers.features.pictures import service as pictures
from ofmhelpers.features.repurpose import service as repurpose
from ofmhelpers.features.threads import tasks as threads
from ofmhelpers.features.uniqify import service as uniqify
lazy from ofmhelpers.schemas import Job

RESUMABLE: frozendict[str, Callable[..., Any]] = frozendict(
    {
        generate.TASK: generate.run_ai,
        clone.TASK: run_clone,
        clone_links.TASK: run_clone_link,
        clone_long_page.TASK: run_long_clone,
        pictures.TASK: pictures.run_picture,
    }
)
JOB_TIMEOUTS: frozendict[str, Callable[[dict[str, Any]], int]] = frozendict(
    {clone_long_page.TASK: clone_long_time_limit}
)
QUEUES = frozendict({clone_links.TASK: QueueName.CLONE_LINKS})


def _stored(job: Job) -> dict[str, Any]:
    return {name: value for name, value in job.params.items() if name != COUNT}


def _in_its_folder(job: Job) -> dict[str, Any]:
    params = _stored(job)
    return {"job_dir": params.pop("dir"), **params}


def _with_assets_root(job: Job) -> dict[str, Any]:
    return {**_stored(job), "assets_root": str(assets_root_for_actor(job.actor))}


def _sheet(job: Job) -> dict[str, Any]:
    return {
        "job_dir": job.params["dir"],
        "files": job.params["files"],
        "assets_root": str(assets_root_for_actor(job.actor)),
    }


def _threads(job: Job) -> dict[str, Any]:
    return {
        **threads.PicturesRun.model_validate(job.params).model_dump(),
        "user_id": int(job.actor or ""),
        "job_id": job.id,
    }


def _download(job: Job) -> dict[str, Any]:
    actor = job.actor
    return {
        "urls": job.params["urls"],
        "assets_root": str(assets_root_for_actor(actor)),
        "user_id": int(actor) if actor and actor.isdigit() else None,
        "job_id": job.id,
    }


RESTARTS = frozendict(
    {
        uniqify.TASK: Restart(run=uniqify.run_uniqify, kwargs=_in_its_folder),
        repurpose.TASK: Restart(
            run=repurpose.run_repurpose,
            kwargs=_in_its_folder,
            queue=QueueName.MEDIA,
            job_timeout=lambda _params: settings.repurpose.job_timeout_s,
        ),
        character_sheet.TASK: Restart(run=character_sheet.run_combine, kwargs=_sheet),
        edit_service.TASK: Restart(
            run=edit_service.run_cut,
            kwargs=_with_assets_root,
            queue=QueueName.EDIT,
            job_timeout=edit_service.cut_timeout_s,
        ),
        edit_service.PREPARE_TASK: Restart(
            run=edit_service.run_prepare,
            kwargs=_with_assets_root,
            queue=QueueName.EDIT,
            job_timeout=lambda _params: settings.web.edit_prepare_timeout_s + 60,
        ),
        handmade.TASK: Restart(
            run=handmade.run_handmade,
            kwargs=lambda job: {**_in_its_folder(job), "job_id": job.id},
        ),
        download_media.TASK: Restart(
            run=download_media.run_downloads, kwargs=_download
        ),
        download_videos.TASK: Restart(
            run=download_videos.run_downloads, kwargs=_download
        ),
        download_images.TASK: Restart(
            run=download_images.run_downloads, kwargs=_download
        ),
        threads.TASK: Restart(
            run=threads.make_pictures,
            kwargs=_threads,
            queue=QueueName.MEDIA,
            job_timeout=lambda _params: settings.threads.job_timeout_s,
        ),
    }
)
