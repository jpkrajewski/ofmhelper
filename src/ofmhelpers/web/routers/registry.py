"""Every background-job task the app runs, by the task name its jobs are
stored under: its label and the router that serves it. The /generate and
/download-assets galleries and the admin action log all read this, so a new
tool is one entry here instead of one in each of them."""

from ofmhelpers.web.routers.downloads import clean_image, images, videos
from ofmhelpers.web.routers.generation import (
    clone,
    fake_ai,
    gpt_image,
    kling,
    minimax,
    nbp,
    seedance,
    seedance25,
    seedream5,
    seedream45,
    wan,
)
from ofmhelpers.web.routers.generation.clone import _run_clone
from ofmhelpers.web.routers.generation.gpt_image import _run_gpt_image
from ofmhelpers.web.routers.generation.kling import _run_kling3
from ofmhelpers.web.routers.generation.minimax import _run_minimax_h3
from ofmhelpers.web.routers.generation.nbp import _run_nanobanana
from ofmhelpers.web.routers.generation.seedance import _run_seedance
from ofmhelpers.web.routers.generation.seedance25 import _run_seedance25
from ofmhelpers.web.routers.generation.seedream5 import _run_seedream5
from ofmhelpers.web.routers.generation.seedream45 import _run_seedream45
from ofmhelpers.web.routers.generation.wan import _run_wan3
from ofmhelpers.web.routers.image_tools import character_sheet
from ofmhelpers.web.routers.task_helpers import job_assets
from ofmhelpers.web.routers.video_tools import uniqify
from ofmhelpers.web.schemas import Job, TaskCard, Tool

# The /generate gallery, in its picker's order.
GENERATE_TOOLS = {
    "seedance": Tool(label="Seedance 2.0", router=seedance.router, run=_run_seedance),
    "seedance25": Tool(
        label="Seedance 2.5", router=seedance25.router, run=_run_seedance25
    ),
    "kling3": Tool(label="Kling 3.0", router=kling.router, run=_run_kling3),
    "wan3": Tool(label="Wan 3.0", router=wan.router, run=_run_wan3),
    "minimax_h3": Tool(label="MiniMax H3", router=minimax.router, run=_run_minimax_h3),
    "nanobanana": Tool(label="Nano Banana Pro", router=nbp.router, run=_run_nanobanana),
    "seedream45": Tool(
        label="Seedream 4.5", router=seedream45.router, run=_run_seedream45
    ),
    "seedream5": Tool(
        label="Seedream 5.0", router=seedream5.router, run=_run_seedream5
    ),
    "gpt_image": Tool(
        label="GPT Image 2.5 Flare",
        router=gpt_image.router,
        run=_run_gpt_image,
    ),
    "fake_ai": Tool(label="Fake AI Model", router=fake_ai.router),
}

# The /download-assets gallery.
DOWNLOAD_TOOLS = {
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
    return TaskCard.from_job(
        job,
        *job_assets(job, tool.files_prefix),
        task_label=tool.label,
        poll_prefix=tool.prefix,
    )
