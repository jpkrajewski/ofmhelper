from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict


class Tool(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    label: str
    router: APIRouter
    describe: Callable[[Any], str] | None = None
    form_params: Callable[[Any], dict[str, Any]] | None = None

    @property
    def prefix(self) -> str:
        return self.router.prefix

    @property
    def files_prefix(self) -> str:
        return f"{self.router.prefix}/files"
