"""APIYI's model families: the APIs one APIYI key reaches, one module each.
A family knows which model ids it serves (`prefixes`), how to submit one and
read its task back; the provider (`apiyi/provider.py`) only dispatches.

A task id carries its family (`<tag>:<id>`) so a status read, a resume and
recovery know which API it belongs to; DashScope's (Wan 3.0, the first one)
stay bare. A new family = a module here + its entry in `families()`.
"""

from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from ofmhelpers.integrations.ai_providers.apiyi.client import ApiyiClient
from ofmhelpers.integrations.ai_providers.base import TaskState
from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

_SEP = ":"


class Family(ABC):
    """One API behind APIYI."""

    # Task id prefix; "" = bare ids (DashScope).
    tag: ClassVar[str]
    # Model ids it serves, by prefix (lower-case).
    prefixes: ClassVar[tuple[str, ...]]

    def serves(self, model_id: str) -> bool:
        return model_id.lower().startswith(self.prefixes)

    @abstractmethod
    def submit(
        self, client: ApiyiClient, spec: ModelSpec, payload: Payload, job: str
    ) -> str:
        """Start `spec` with `payload` for our `job`; this family's own id."""

    @abstractmethod
    def status(self, client: ApiyiClient, task_id: str) -> TaskState:
        """One read of a task this family made (its own id)."""

    def download(self, client: ApiyiClient, url: str, dest: Path) -> Path | None:  # noqa: ARG002
        """Save a result this family reads itself (behind our key, or kept on
        this server); None = the provider's plain download."""
        return None

    def task_id(self, own_id: str) -> str:
        return f"{self.tag}{_SEP}{own_id}" if self.tag else own_id


@lru_cache(maxsize=1)
def families() -> tuple[Family, ...]:
    """Every family, the first serving a model id wins (a singleton)."""
    from ofmhelpers.integrations.ai_providers.apiyi.families import (
        dashscope,
        gemini,
        images,
        seedance,
        veo,
    )

    return (
        dashscope.DashScope(),
        seedance.Seedance(),
        veo.Veo(),
        gemini.Gemini(),
        images.GptImage(),
        images.Seedream(),
        images.Flux(),
    )


def family_for(model_id: str) -> Family:
    found = next((f for f in families() if f.serves(model_id)), None)
    if found is None:
        msg = f"APIYI: no family serves {model_id}"
        raise KeyError(msg)
    return found


def split(task_id: str) -> tuple[Family, str]:
    """The family a task id belongs to, and that family's own id."""
    tag, sep, own = task_id.partition(_SEP)
    if sep:
        found = next((f for f in families() if f.tag and f.tag == tag), None)
        if found is not None:
            return found, own
    bare = next(f for f in families() if not f.tag)
    return bare, task_id
