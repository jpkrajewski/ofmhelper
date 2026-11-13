"""What /generate's form shows besides its gallery: the providers and the
one it starts on, every model with its fields, and the page's limits."""

from typing import Any

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.features.generate.choices import model_choices, provider_choices
from ofmhelpers.features.generate.providers import start_provider
from ofmhelpers.schemas import ProviderChoice

TASKS = frozenset({"ai"})


class FormView(BaseModel):
    providers: list[ProviderChoice]
    selected_provider: str
    # Each model as the page's JSON catalog holds it (ai-form.js).
    models: list[dict[str, Any]]
    live_poll_s: int
    # The most images one submit may queue (each its own job and card).
    max_batch: int


def form_view(actor: str) -> FormView:
    providers = provider_choices()
    return FormView(
        providers=providers,
        selected_provider=start_provider(actor, TASKS, [p.name for p in providers]),
        models=[m.model_dump() for m in model_choices()],
        live_poll_s=settings.web.gallery_live_poll_s,
        max_batch=settings.aigen.max_batch,
    )
