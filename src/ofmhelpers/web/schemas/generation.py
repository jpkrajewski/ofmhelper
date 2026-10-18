"""What the generation pages offer: the providers to pick from and every
model of each, as the page script filters them. Built from the provider
registry and the catalog (`aigenproviders/`), so a new provider or model
appears without touching a template.
"""

from typing import Literal

from pydantic import BaseModel

from ofmhelpers.aigenproviders.base import provider_classes
from ofmhelpers.aigenproviders.catalog import catalog


class ProviderChoice(BaseModel):
    name: str
    label: str
    needs_key: bool

    @classmethod
    def all(cls, names: list[str] | None = None) -> list["ProviderChoice"]:
        """Every provider, or just `names` in that order."""
        classes = provider_classes()
        return [
            cls(name=name, label=classes[name].label, needs_key=classes[name].needs_key)
            for name in (names or list(classes))
        ]


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

    @classmethod
    def all(cls) -> list["ModelChoice"]:
        return [
            cls(
                key=spec.key,
                provider=spec.provider,
                label=spec.label,
                family=spec.family,
                kind=spec.kind,
                prompt=(
                    "required"
                    if "prompt" in spec.required
                    else "optional"
                    if "prompt" in spec.properties
                    else "none"
                ),
                default_prompt=spec.default_prompt,
            )
            for spec in catalog().values()
        ]
