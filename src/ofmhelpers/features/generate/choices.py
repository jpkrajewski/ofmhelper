"""The providers and models the generation pages offer, read off the provider
registry and the catalog (`integrations/ai_providers/`)."""

from ofmhelpers.integrations.ai_providers.base import provider_classes
from ofmhelpers.integrations.ai_providers.catalog import catalog
from ofmhelpers.schemas import ModelChoice, ProviderChoice


def provider_choices(
    names: list[str] | None = None, *, notices: bool = True
) -> list[ProviderChoice]:
    """Every provider, or just `names` in that order. `notices`: with each
    one's passing warning (off for a page its warning is not about)."""
    classes = provider_classes()
    return [
        ProviderChoice(
            name=name,
            label=classes[name].label,
            needs_key=classes[name].needs_key,
            notice=classes[name].notice() if notices else "",
        )
        for name in (names or classes.names())
    ]


def model_choices() -> list[ModelChoice]:
    return [
        ModelChoice(
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
        for spec in catalog().all()
    ]
