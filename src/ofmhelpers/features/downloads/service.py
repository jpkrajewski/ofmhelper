"""What the download tools share: a pasted box becomes one queued job, and
the answer that sends the page on to another one."""

from collections.abc import Callable
from typing import Any
lazy from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.integrations.downloaders.urls import read_links
from ofmhelpers.schemas import JobQueued


class Redirect(BaseModel):
    """The URL the page opens next, with the picked videos on its query."""

    redirect: str


def queue_downloads(
    task: str,
    pasted: str,
    run: Callable[..., Any],
    actor: str,
    owner_id: int,
    assets_root: Path,
) -> JobQueued:
    """One job of `task` for the links pasted in the box, run by `run`
    (`run_downloads` of the tool's own module: RQ stores that path)."""
    links = read_links(pasted)
    if not links:
        msg = "At least one URL is required"
        raise RefusedError(msg)
    job_id = create_job(task, {"urls": links}, actor=actor)
    enqueue(
        run_job,
        job_id,
        run,
        {"urls": links, "assets_root": str(assets_root), "user_id": owner_id},
    )
    return JobQueued(job_id=job_id)
