"""What the generation pages offer: the providers to pick from and every
model of each, as the page script filters them. `features/generate/choices.py`
builds them from the provider registry and the catalog
(`integrations/ai_providers/`), so a new provider or model appears without
touching a template.
"""

from typing import Literal

from pydantic import BaseModel


class ProviderChoice(BaseModel):
    """A provider as the select lists it. `notice`: a passing warning shown
    while it is picked ("" = none)."""

    name: str
    label: str
    needs_key: bool
    notice: str = ""


class KeySlot(BaseModel):
    """One API key a person keeps for a provider (/account): most providers
    have one, named like the provider; a provider whose key reaches only some
    of its models (APIYI's token groups) has one per group, the first its
    default. `id` is what `user_api_keys.provider` stores."""

    id: str
    label: str


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
