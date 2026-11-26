# Copyright (C) 2026 jpkrajewski

from fastapi import APIRouter

from ofmhelpers.db.jobs.schema import Job, JobParams
from ofmhelpers.features.generate.legacy import legacy_tools
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access
from ofmhelpers.web.routers.task_helpers import add_job_routes


def as_ai_params(job: Job) -> JobParams:
    tool = legacy_tools().get(job.task)
    if tool is None or tool.as_ai is None:
        return JobParams(params=job.params)
    return JobParams(
        params={"provider": "kie", **tool.as_ai.translate(job.params).model_dump()},
    )


def _router(prefix: str, label: str) -> APIRouter:
    router = APIRouter(
        prefix=prefix,
        tags=["legacy"],
        dependencies=[Access(Area.LEGACY)],
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
    for task, tool in legacy_tools().items()
}
