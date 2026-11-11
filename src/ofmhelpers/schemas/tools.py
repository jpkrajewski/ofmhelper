"""A background-job tool as the app's task registry (`routers/registry.py`)
knows it. What recovery does with its jobs: `features/jobs/registry.py`."""

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
    # A card label read off the job itself, for a tool whose jobs differ
    # (/ai: the model and provider); `label` otherwise.
    describe: Callable[[Any], str] | None = None
    # The job's params as the /generate form's fields, for Recreate, when
    # they are stored under other names (an old per-model tool's).
    form_params: Callable[[Any], dict[str, Any]] | None = None

    @property
    def prefix(self) -> str:
        return self.router.prefix

    @property
    def files_prefix(self) -> str:
        return f"{self.router.prefix}/files"
