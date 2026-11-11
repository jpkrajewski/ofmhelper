from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from ofmhelpers.features.downloads import clean

# Jobs queued before 2026-11-11 name the run function by this module's path.
from ofmhelpers.features.downloads.clean import run_clean as _run_clean
from ofmhelpers.schemas import JobQueued
from ofmhelpers.web.dependencies import Actor, AssetsRoot
from ofmhelpers.web.routers.task_helpers import add_job_routes

__all__ = ["_run_clean", "router"]

router = APIRouter(prefix="/clean-images", tags=["clean-images"])


@router.post("/run", status_code=202)
def run(
    actor: Actor,
    assets_root: AssetsRoot,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> JobQueued:
    return clean.start(files or [], actor, assets_root)


add_job_routes(
    router,
    "Clean images",
    lambda job: f"Cleaning {len(job.params['files'])} image(s)…",
    back_url="/download-assets",
    back_label="clean more images",
)
