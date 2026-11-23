# Copyright (C) 2026 jpkrajewski

from typing import Any
lazy from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.features.assets.manifests import keep_videos
from ofmhelpers.features.clone.addons import PromptAddon, prompt_addons
from ofmhelpers.features.clone.service import SubjectPicks, subject_picks
from ofmhelpers.features.clone_pricing.pricing import clone_pricing
from ofmhelpers.features.clone_pricing.targets import (
    DEFAULT_RESOLUTION,
    clone_targets,
    offered_resolutions,
)
from ofmhelpers.features.generate.choices import provider_choices
from ofmhelpers.features.generate.providers import CLONE_TASKS, start_provider
from ofmhelpers.integrations.ai_providers.schema import ProviderChoice
from ofmhelpers.media.exports import Export, exports


class FormView(BaseModel):
    providers: list[ProviderChoice]
    selected_provider: str
    pricing: dict[str, dict[str, Any]]
    default_prompt: str
    default_resolution: str
    resolutions: list[str]
    max_videos: int
    max_images: int
    live_poll_s: int
    prefill_videos: list[str]
    subjects: SubjectPicks
    addons: list[PromptAddon]
    max_reference_s: int
    exports: list[Export]
    default_export: str
    default_mirror: bool


def form_view(
    actor: str,
    team: frozenset[str],
    assets_root: Path,
    videos: list[str],
) -> FormView:
    targets = clone_targets()
    return FormView(
        providers=provider_choices(list(targets)),
        selected_provider=start_provider(actor, CLONE_TASKS, list(targets)),
        pricing={name: price.model_dump() for name, price in clone_pricing().items()},
        default_prompt=settings.web.clone_default_prompt,
        default_resolution=DEFAULT_RESOLUTION,
        resolutions=offered_resolutions(),
        max_videos=settings.web.clone_max_videos,
        max_images=settings.web.clone_max_images,
        live_poll_s=settings.web.gallery_live_poll_s,
        prefill_videos=keep_videos(videos, assets_root),
        subjects=subject_picks(team, assets_root),
        addons=prompt_addons(),
        max_reference_s=min(t.max_reference_s for t in targets.values()),
        exports=list(exports().values()),
        default_export=settings.exports.default,
        default_mirror=settings.exports.mirror,
    )
