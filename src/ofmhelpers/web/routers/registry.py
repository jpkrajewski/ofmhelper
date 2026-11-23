# Copyright (C) 2026 jpkrajewski

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ofmhelpers.db.jobs.schema import Job, JobParams
from ofmhelpers.features.clone_links.constants import (
    CloneLinksTask,
    clone_links_constants,
)
from ofmhelpers.features.jobs.models import TaskCard
from ofmhelpers.features.threads.tasks import TASK as THREADS_TASK
from ofmhelpers.web.routers.downloads import clean_image, images, media, videos, viral
from ofmhelpers.web.routers.generation import ai, clone, clone_links, clone_long
from ofmhelpers.web.routers.generation.legacy import LEGACY_TOOLS, as_ai_params
from ofmhelpers.web.routers.image_tools import character_sheet, pictures, threads
from ofmhelpers.web.routers.inventory import handmade
from ofmhelpers.web.routers.task_helpers import job_assets
from ofmhelpers.web.routers.video_tools import uniqify


class Tool(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    label: str
    router: APIRouter
    describe: Callable[[Any], str] | None = None
    form_params: Callable[[Any], JobParams] | None = None

    @property
    def prefix(self) -> str:
        return self.router.prefix

    @property
    def files_prefix(self) -> str:
        return f"{self.router.prefix}/files"


GENERATE_TOOLS = frozendict(
    {
        ai.TASK: Tool(label="AI generation", router=ai.router, describe=ai.describe),
        **{
            task: Tool(label=label, router=router, form_params=as_ai_params)
            for task, (label, router) in LEGACY_TOOLS.items()
        },
    },
)

DOWNLOAD_TOOLS = frozendict(
    {
        media.TASK: Tool(label="Download", router=media.router),
        "download_videos": Tool(label="Download videos", router=videos.router),
        "download_images": Tool(label="Download images", router=images.router),
        "clean_images": Tool(label="Clean images", router=clean_image.router),
    },
)


TOOLS = frozendict(
    {
        **GENERATE_TOOLS,
        **DOWNLOAD_TOOLS,
        "clone": Tool(label="Clone", router=clone.router),
        CloneLinksTask.RUN: Tool(
            label=clone_links_constants().label,
            router=clone_links.router,
        ),
        clone_long.TASK: Tool(
            label=clone_long.LABEL,
            router=clone_long.router,
        ),
        "character_sheet": Tool(label="Character sheet", router=character_sheet.router),
        "uniqify": Tool(label="Uniqify", router=uniqify.router),
        viral.TASK: Tool(label=viral.LABEL, router=viral.router),
        handmade.TASK: Tool(label="Handmade", router=handmade.router),
        THREADS_TASK: Tool(label="Threads", router=threads.router),
        pictures.TASK: Tool(
            label=pictures.LABEL,
            router=pictures.router,
            describe=pictures.describe,
        ),
    },
)


def task_card(job: Job) -> TaskCard:
    tool = TOOLS[job.task]
    if tool.form_params:
        job = job.model_copy(update={"params": tool.form_params(job).params})
    found = job_assets(job, tool.files_prefix)
    return TaskCard.from_job(
        job,
        found.assets,
        found.failed_sources,
        {
            "task_label": tool.describe(job) if tool.describe else tool.label,
            "poll_prefix": tool.prefix,
        },
    )
