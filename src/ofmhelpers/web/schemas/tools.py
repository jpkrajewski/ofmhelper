"""A background-job tool as the app's task registry (`routers/registry.py`)
knows it."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict


class Tool(BaseModel):
    """A tool's display label and the router serving its jobs: status page,
    polling payload and files all live under the router's prefix."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    label: str
    router: APIRouter
    # The worker function its jobs run, for a kie.ai tool: what recovery
    # re-runs a job with once its task finishes after the job gave up.
    run: Callable[..., Any] | None = None

    @property
    def prefix(self) -> str:
        return self.router.prefix

    @property
    def files_prefix(self) -> str:
        return f"{self.router.prefix}/files"
