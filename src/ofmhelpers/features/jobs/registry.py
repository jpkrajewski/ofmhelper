"""What recovery does with each task, by the name its jobs are stored under:
the run function a paid task continues with (`RESUMABLE`), its time limit and
queue where not the default, and the local tasks a restart starts over
(`RESTARTS`). A new tool that should survive a deploy is one entry here; its
label and pages are `web/routers/registry.py`."""

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
from ofmhelpers.features.jobs.restart import COUNT, Restart
from ofmhelpers.features.pictures import service as pictures
from ofmhelpers.features.repurpose import service as repurpose
from ofmhelpers.features.uniqify import service as uniqify
lazy from ofmhelpers.schemas import Job

# Tasks recovery can finish by re-running the job (see recovery.py).
RESUMABLE: frozendict[str, Callable[..., Any]] = frozendict(
    {
        generate.TASK: generate.run_ai,
        clone.TASK: run_clone,
        clone_links.TASK: run_clone_link,
        clone_long_page.TASK: run_long_clone,
        pictures.TASK: pictures.run_picture,
    }
)
# Their RQ time limits, where longer than the queue's default.
JOB_TIMEOUTS: frozendict[str, Callable[[dict[str, Any]], int]] = frozendict(
    {clone_long_page.TASK: clone_long_time_limit}
)
# The RQ queue a task's jobs run on, where not the default one: its first
# run (its feature's submit) and recovery's re-run alike. A /clone-links Generate has
# its own worker pool so it never holds the default queue.
QUEUES = frozendict({clone_links.TASK: QueueName.CLONE_LINKS})


def _stored(job: Job) -> dict[str, Any]:
    """The job's own params, without recovery's count of its restarts."""
    return {name: value for name, value in job.params.items() if name != COUNT}


def _in_its_folder(job: Job) -> dict[str, Any]:
    """A job stored as its upload folder (`dir`) plus exactly what its run
    takes."""
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


def _download(job: Job) -> dict[str, Any]:
    """As the route queued it: the person's part of the store, and their own
    cookies (a job's actor is its user's id)."""
    actor = job.actor
    return {
        "urls": job.params["urls"],
        "assets_root": str(assets_root_for_actor(actor)),
        "user_id": int(actor) if actor and actor.isdigit() else None,
        "job_id": job.id,
    }


# Tasks a restart may cut off and recovery starts over from their stored
# params (features/jobs/restart.py): local work only (ffmpeg, a download, a
# sheet from uploads), free and the same when run twice. NEVER a task that
# pays a provider: those continue from their stored provider task id
# (`RESUMABLE` above) or fail "Interrupted by a restart". Not here on purpose:
# - viral_reels: every run pays Apify for the scan and keeps no run id.
# - clean_images: rewrites its uploads in place, so a file cut off half
#   written breaks the rerun; it runs seconds and finishes inside the
#   workers' 120 s.
# - trials: its own restart (features/trials/folder.py), Generate only.
RESTARTS = frozendict(
    {
        uniqify.TASK: Restart(run=uniqify.run_uniqify, kwargs=_in_its_folder),
        repurpose.TASK: Restart(
            run=repurpose.run_repurpose,
            kwargs=_in_its_folder,
            job_timeout=lambda params: (
                params["copies"] * settings.web.uniqify_encode_timeout_s
            ),
        ),
        character_sheet.TASK: Restart(run=character_sheet.run_combine, kwargs=_sheet),
        edit_service.TASK: Restart(
            run=edit_service.run_cut,
            kwargs=_with_assets_root,
            queue=QueueName.EDIT,
            job_timeout=lambda _params: settings.web.edit_cut_timeout_s + 60,
        ),
        edit_service.PREPARE_TASK: Restart(
            run=edit_service.run_prepare,
            kwargs=_with_assets_root,
            queue=QueueName.EDIT,
            job_timeout=lambda _params: settings.web.edit_prepare_timeout_s + 60,
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
    }
)
