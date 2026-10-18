"""Job pages of the per-model tools /ai replaced, kept so their links work.

Jobs created before /ai (task `seedance`, `kling3`, ...) still sit in every
gallery, and their cards, status pages and file links point under the old
prefixes. These routers serve exactly those three job routes per old tool,
and nothing else: there is no /run, so no new job is ever created here.
"""

from fastapi import APIRouter

from ofmhelpers.web.routers.task_helpers import add_job_routes

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
