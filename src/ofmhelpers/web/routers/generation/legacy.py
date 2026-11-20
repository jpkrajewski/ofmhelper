from typing import Any

from fastapi import APIRouter

from ofmhelpers.auth.access import Area
from ofmhelpers.features.generate.legacy import legacy_tools
from ofmhelpers.schemas import Job
from ofmhelpers.web.dependencies import Access
from ofmhelpers.web.routers.task_helpers import add_job_routes


def as_ai_params(job: Job) -> dict[str, Any]:
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


LEGACY_TOOLS = {
    task: (tool.label, _router(tool.prefix, tool.label))
    for task, tool in legacy_tools().entries.items()
}
