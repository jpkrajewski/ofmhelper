from typing import Any
lazy from collections.abc import Callable

from ofmhelpers.cache import QueueName
from ofmhelpers.config import settings
from ofmhelpers.features.assets.store import assets_root_for_actor
from ofmhelpers.features.character_sheet import service as character_sheet
from ofmhelpers.features.clone import service as clone
from ofmhelpers.features.clone.run import run_clone
from ofmhelpers.features.clone_links import review as clone_links_review
from ofmhelpers.features.clone_links import service as clone_links
from ofmhelpers.features.clone_links.job import run_clone_link
from ofmhelpers.features.clone_long import page as clone_long_page
from ofmhelpers.features.clone_long import run_long_clone
from ofmhelpers.features.clone_long import time_limit as clone_long_time_limit
from ofmhelpers.features.cookies import service as cookies
from ofmhelpers.features.distribution import publish as pinterest
from ofmhelpers.features.downloads import clean as clean_images
from ofmhelpers.features.downloads import restarts as download_restarts
from ofmhelpers.features.edit import service as edit_service
from ofmhelpers.features.generate import service as generate
from ofmhelpers.features.handmade import service as handmade
from ofmhelpers.features.jobs.restart import COUNT, Restart, RunArgs
from ofmhelpers.features.links import page as links
from ofmhelpers.features.pictures import service as pictures
from ofmhelpers.features.reels.watch import unlock_gated
from ofmhelpers.features.reports import asks as report_asks
from ofmhelpers.features.repurpose import service as repurpose
from ofmhelpers.features.storage import jobs as storage
from ofmhelpers.features.storage.constants import storage_constants
from ofmhelpers.features.storage.models import BinMove
from ofmhelpers.features.threads import tasks as threads
from ofmhelpers.features.uniqify import service as uniqify
from ofmhelpers.features.viral import page as viral
lazy from ofmhelpers.db.jobs.schema import Job

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


def _stored(job: Job) -> RunArgs:
    return RunArgs.model_validate(
        {name: value for name, value in job.params.items() if name != COUNT}
    )


def _in_its_folder(job: Job) -> RunArgs:
    params = _stored(job).model_dump()
    return RunArgs.model_validate({"job_dir": params.pop("dir"), **params})


def _with_assets_root(job: Job) -> RunArgs:
    return RunArgs.model_validate(
        {
            **_stored(job).model_dump(),
            "assets_root": str(assets_root_for_actor(job.actor)),
        }
    )


def _handmade(job: Job) -> RunArgs:
    return RunArgs.model_validate(
        {**_in_its_folder(job).model_dump(), "job_id": job.id}
    )


def _sheet(job: Job) -> RunArgs:
    return RunArgs.model_validate(
        {
            "job_dir": job.params["dir"],
            "files": job.params["files"],
            "assets_root": str(assets_root_for_actor(job.actor)),
        }
    )


def _threads(job: Job) -> RunArgs:
    return RunArgs.model_validate(
        {
            **threads.PicturesRun.model_validate(job.params).model_dump(),
            "user_id": int(job.actor or ""),
            "job_id": job.id,
        }
    )


def _owner(job: Job) -> RunArgs:
    return RunArgs.model_validate({"user_id": int(job.actor or "")})


def _storage_scan(job: Job) -> RunArgs:
    return RunArgs.model_validate({**_owner(job).model_dump(), "job_id": job.id})


def _bin_move(job: Job) -> RunArgs:
    return RunArgs.model_validate(
        {
            **_storage_scan(job).model_dump(),
            **BinMove.model_validate(job.params).model_dump(mode="json"),
        }
    )


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
        handmade.TASK: Restart(run=handmade.run_handmade, kwargs=_handmade),
        cookies.UNLOCK_TASK: Restart(run=unlock_gated, kwargs=_owner),
        **download_restarts.RESTARTS,
        threads.TASK: Restart(
            run=threads.make_pictures,
            kwargs=_threads,
            queue=QueueName.MEDIA,
            job_timeout=lambda _params: settings.threads.job_timeout_s,
        ),
        storage_constants().scan_task: Restart(
            run=storage.run_scan,
            kwargs=_storage_scan,
            job_timeout=lambda _params: settings.storage.scan_timeout_s,
        ),
        storage_constants().bin_task: Restart(
            run=storage.run_bin,
            kwargs=_bin_move,
            job_timeout=lambda _params: settings.storage.clean_timeout_s,
        ),
    }
)

DRAINED = frozenset(
    {
        storage_constants().upload_task,
        storage_constants().sync_task,
        links.CREATE_TASK,
        links.ADULT_TASK,
        links.DELETE_TASK,
        report_asks.TASK,
        viral.TASK,
        clean_images.TASK,
    }
)
RECOVERED_BY_THEIR_SWEEP = frozenset({pinterest.TASK, clone_links_review.TASK})
