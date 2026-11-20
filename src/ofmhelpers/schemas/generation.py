from typing import Literal

from pydantic import BaseModel


class ProviderChoice(BaseModel):
    name: str
    label: str
    needs_key: bool
    notice: str = ""


class KeySlot(BaseModel):
    id: str
    label: str


class ModelChoice(BaseModel):
    key: str
    provider: str
    label: str
    family: str
    kind: Literal["image", "video"]
    prompt: Literal["required", "optional", "none"]
    default_prompt: str | None = None
