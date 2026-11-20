from abc import ABC, abstractmethod
from functools import lru_cache
from typing import ClassVar
lazy from pathlib import Path

from pydantic import BaseModel, ConfigDict

lazy from ofmhelpers.integrations.ai_providers.apiyi.client import ApiyiClient
lazy from ofmhelpers.integrations.ai_providers.apiyi.families import (
    dashscope,
    gemini,
    images,
    seedance,
    veo,
)
lazy from ofmhelpers.integrations.ai_providers.base import TaskState
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

_SEP = ":"


class Family(ABC):
    tag: ClassVar[str]
    prefixes: ClassVar[tuple[str, ...]]

    def serves(self, model_id: str) -> bool:
        return model_id.lower().startswith(self.prefixes)

    @abstractmethod
    def submit(
        self, client: ApiyiClient, spec: ModelSpec, payload: Payload, job: str
    ) -> str: ...

    @abstractmethod
    def status(self, client: ApiyiClient, task_id: str) -> TaskState: ...

    def download(self, client: ApiyiClient, url: str, dest: Path) -> Path | None:  # noqa: ARG002
        return None

    def task_id(self, own_id: str) -> str:
        return f"{self.tag}{_SEP}{own_id}" if self.tag else own_id


@lru_cache(maxsize=1)
def families() -> list[Family]:
    return [
        dashscope.DashScope(),
        seedance.Seedance(),
        veo.Veo(),
        gemini.Gemini(),
        images.GptImage(),
        images.Seedream(),
        images.Flux(),
    ]


def family_for(model_id: str) -> Family:
    found = next((f for f in families() if f.serves(model_id)), None)
    if found is None:
        msg = f"APIYI: no family serves {model_id}"
        raise KeyError(msg)
    return found


class FamilyTask(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    family: Family
    own_id: str


def split(task_id: str) -> FamilyTask:
    tag, sep, own = task_id.partition(_SEP)
    if sep:
        found = next((f for f in families() if f.tag and f.tag == tag), None)
        if found is not None:
            return FamilyTask(family=found, own_id=own)
    bare = next(f for f in families() if not f.tag)
    return FamilyTask(family=bare, own_id=task_id)
