"""What the generation pages offer: the providers to pick from and every
model of each, as the page script filters them. `features/generate/choices.py`
builds them from the provider registry and the catalog
(`integrations/ai_providers/`), so a new provider or model appears without
touching a template.
"""

from typing import Literal

from pydantic import BaseModel


class ProviderChoice(BaseModel):
    name: str
    label: str
    needs_key: bool


class ModelChoice(BaseModel):
    """A model as the picker lists it. `prompt` says whether the shared
    prompt box applies: required, optional, or not taken at all."""

    key: str
    provider: str
    label: str
    family: str
    kind: Literal["image", "video"]
    prompt: Literal["required", "optional", "none"]
    default_prompt: str | None = None
