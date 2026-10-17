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
from ofmhelpers.web.routers.image_tools import character_sheet
from ofmhelpers.web.routers.task_helpers import job_assets
from ofmhelpers.web.routers.video_tools import uniqify
from ofmhelpers.web.schemas import Job, TaskCard, Tool

# The /generate gallery, in its picker's order.
GENERATE_TOOLS = {
    "seedance": Tool(label="Seedance 2.0", router=seedance.router),
    "seedance25": Tool(label="Seedance 2.5", router=seedance25.router),
    "kling3": Tool(label="Kling 3.0", router=kling.router),
    "wan3": Tool(label="Wan 3.0", router=wan.router),
    "minimax_h3": Tool(label="MiniMax H3", router=minimax.router),
    "nanobanana": Tool(label="Nano Banana Pro", router=nbp.router),
    "seedream45": Tool(label="Seedream 4.5", router=seedream45.router),
    "seedream5": Tool(label="Seedream 5.0", router=seedream5.router),
    "gpt_image": Tool(label="GPT Image 2.5 Flare", router=gpt_image.router),
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
    "clone": Tool(label="Clone", router=clone.router),
    "character_sheet": Tool(label="Character sheet", router=character_sheet.router),
    "uniqify": Tool(label="Uniqify", router=uniqify.router),
}


def task_card(job: Job) -> TaskCard:
    """A job as a card in a gallery that mixes several tools."""
    tool = TOOLS[job.task]
    return TaskCard.from_job(
        job,
        *job_assets(job, tool.files_prefix),
        task_label=tool.label,
        poll_prefix=tool.prefix,
    )
