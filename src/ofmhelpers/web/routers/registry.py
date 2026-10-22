"""Every background-job task the app runs, by the task name its jobs are
stored under: its label and the router that serves it. The /generate and
/download-assets galleries and the admin action log all read this, so a new
tool is one entry here instead of one in each of them."""

from ofmhelpers.web.routers.downloads import clean_image, images, media, videos
from ofmhelpers.web.routers.generation import ai, clone
from ofmhelpers.web.routers.generation.clone import _run_clone
from ofmhelpers.web.routers.generation.legacy import LEGACY_TOOLS, as_ai_params
from ofmhelpers.web.routers.image_tools import character_sheet
from ofmhelpers.web.routers.task_helpers import job_assets
from ofmhelpers.web.routers.video_tools import uniqify
from ofmhelpers.web.schemas import Job, TaskCard, Tool

# The /generate gallery: every /ai job, plus the jobs of the per-model tools
# it replaced (their pages still answer, see generation/legacy.py).
GENERATE_TOOLS = {
    ai.TASK: Tool(
        label="AI generation", router=ai.router, run=ai.run_ai, describe=ai.describe
    ),
    **{
        task: Tool(label=label, router=router, form_params=as_ai_params)
        for task, (label, router) in LEGACY_TOOLS.items()
    },
}

# The /download-assets gallery.
DOWNLOAD_TOOLS = {
    media.TASK: Tool(label="Download", router=media.router),
    # Before /download-media picked the downloader itself: their past jobs.
    "download_videos": Tool(label="Download videos", router=videos.router),
    "download_images": Tool(label="Download images", router=images.router),
    "clean_images": Tool(label="Clean images", router=clean_image.router),
}


TOOLS = {
    **GENERATE_TOOLS,
    **DOWNLOAD_TOOLS,
    "clone": Tool(label="Clone", router=clone.router, run=_run_clone),
    "character_sheet": Tool(label="Character sheet", router=character_sheet.router),
    "uniqify": Tool(label="Uniqify", router=uniqify.router),
}


# Tasks recovery can finish by re-running the job (see web/recovery.py).
RESUMABLE = {task: tool.run for task, tool in TOOLS.items() if tool.run}


def task_card(job: Job) -> TaskCard:
    """A job as a card in a gallery that mixes several tools."""
    tool = TOOLS[job.task]
    if tool.form_params:
        job = job.model_copy(update={"params": tool.form_params(job)})
    return TaskCard.from_job(
        job,
        *job_assets(job, tool.files_prefix),
        task_label=tool.describe(job) if tool.describe else tool.label,
        poll_prefix=tool.prefix,
    )
