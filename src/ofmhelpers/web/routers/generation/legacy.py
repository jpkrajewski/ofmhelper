"""Job pages of the per-model tools /ai replaced, kept so their links work.

Jobs created before /ai (task `seedance`, `kling3`, ...) still sit in every
gallery, and their cards, status pages and file links point under the old
prefixes. These routers serve exactly those three job routes per old tool,
and nothing else: there is no /run, so no new job is ever created here.

Their params name no `provider`/`model`, so /generate's Recreate could only
put back the prompt and the next run went to whatever model was picked, with
no references -- a different picture. `as_ai_params` reads an old job as the
/ai job it would be today (catalog model + the schema's field names), so
Recreate restores the model, settings and reference files too.
"""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.schemas import Job

# task name -> (old prefix, label)
_LEGACY = {
    "seedance": ("/seedance", "Seedance 2.0"),
    "seedance25": ("/seedance25", "Seedance 2.5"),
    "kling3": ("/kling3", "Kling 3.0"),
    "wan3": ("/wan3", "Wan 3.0"),
    "minimax_h3": ("/minimax-h3", "MiniMax H3"),
    "nanobanana": ("/nanobanana", "Nano Banana Pro"),
    "seedream45": ("/seedream45", "Seedream 4.5"),
    "seedream5": ("/seedream5", "Seedream 5.0"),
    "gpt_image": ("/gpt-image", "GPT Image 2.5 Flare"),
    "fake_ai": ("/fake-ai", "Fake AI Model"),
}


Params = dict[str, Any]


def _as(key: str, renames: dict[str, str] | None = None) -> Callable[[Params], Params]:
    """An old tool whose params are one catalog model's fields, some under
    an older name."""
    names = renames or {}

    def translate(params: Params) -> Params:
        return {"model": key, **{names.get(k, k): v for k, v in params.items()}}

    return translate


def _seedream5(params: Params) -> Params:
    """One tool for Lite and Pro (`variant`), text- or image-to-image by
    whether references were picked; the catalog has a model per pair."""
    rest = {k: v for k, v in params.items() if k != "variant"}
    mode = "image-to-image" if params.get("image_urls") else "text-to-image"
    return {"model": f"kie:seedream/5-{params.get('variant', 'lite')}-{mode}", **rest}


# task name -> its params as an /ai job's; every old tool ran on kie.ai. Only
# tools with jobs on record: an old task missing here keeps prompt-only
# Recreate.
_AS_AI: dict[str, Callable[[Params], Params]] = {
    "seedream5": _seedream5,
    "nanobanana": _as("kie:nano-banana-pro"),
    "gpt_image": _as("kie:gpt-image-2-5-flare-image-to-image"),
    "wan3": _as(
        "kie:wan/3-0-video",
        {
            "reference_images": "reference_image_urls",
            "reference_videos": "reference_video_urls",
            "reference_audio": "reference_audio_urls",
        },
    ),
}


def as_ai_params(job: Job) -> Params:
    """An old job's params as /ai stores them, or unchanged when its tool
    has no translation."""
    translate = _AS_AI.get(job.task)
    if translate is None:
        return job.params
    return {"provider": "kie", **translate(job.params)}


def _router(prefix: str, label: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["legacy"])
    add_job_routes(
        router,
        label,
        lambda _job: "Generating…",
        back_url="/generate",
        back_label="generate another",
        show_inputs=True,
    )
    return router


# task name -> (label, router)
LEGACY_TOOLS = {
    task: (label, _router(prefix, label)) for task, (prefix, label) in _LEGACY.items()
}
