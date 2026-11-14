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

from typing import Any

from fastapi import APIRouter

from ofmhelpers.auth.access import Area
from ofmhelpers.features.generate.legacy import legacy_tools
from ofmhelpers.schemas import Job
from ofmhelpers.web.dependencies import Access
from ofmhelpers.web.routers.task_helpers import add_job_routes


def as_ai_params(job: Job) -> dict[str, Any]:
    """An old job's params as /ai stores them (every old tool ran on kie.ai),
    or unchanged when its tool has no translation."""
    tool = legacy_tools().get(job.task)
    if tool is None or tool.as_ai is None:
        return job.params
    return {"provider": "kie", **tool.as_ai.translate(job.params).model_dump()}


def _router(prefix: str, label: str) -> APIRouter:
    router = APIRouter(
        prefix=prefix, tags=["legacy"], dependencies=[Access(Area.LEGACY)]
    )
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
    task: (tool.label, _router(tool.prefix, tool.label))
    for task, tool in legacy_tools().entries.items()
}
