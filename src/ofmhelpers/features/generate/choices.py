from ofmhelpers.integrations.ai_providers.base import provider_classes
from ofmhelpers.integrations.ai_providers.catalog import catalog
from ofmhelpers.integrations.ai_providers.schema import ModelChoice, ProviderChoice


def provider_choices(
    names: list[str] | None = None, *, notices: bool = True
) -> list[ProviderChoice]:
    classes = provider_classes()
    return [
        ProviderChoice(
            name=name,
            label=classes[name].label,
            needs_key=classes[name].needs_key,
            notice=classes[name].notice() if notices else "",
        )
        for name in (names or classes.keys())
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
        for spec in catalog().values()
    ]
