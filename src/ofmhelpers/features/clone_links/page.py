"""What the /clone-links page's form shows when it opens
(`clone_links.html`)."""

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.stores.users import refresh_token
from ofmhelpers.features.clone.targets import (
    DEFAULT_RESOLUTION,
    clone_targets,
    offered_resolutions,
)
from ofmhelpers.features.drive.space import DriveSpace, drive_space
from ofmhelpers.features.generate.choices import provider_choices
from ofmhelpers.features.generate.providers import CLONE_TASKS, start_provider
from ofmhelpers.media.exports import Export, exports
from ofmhelpers.schemas import ProviderChoice


class FormView(BaseModel):
    """The form's part of the page; each field is a name the template reads."""

    providers: list[ProviderChoice]
    selected_provider: str
    default_prompt: str
    default_resolution: str
    resolutions: list[str]
    max_images: int
    max_links: int
    live_poll_s: int
    exports: list[Export]
    default_export: str
    default_mirror: bool
    drive_ready: bool
    drive_space: DriveSpace | None
    reel_mb: int


def form_view(actor: str, user_id: int) -> FormView:
    targets = clone_targets().names()
    return FormView(
        providers=provider_choices(targets),
        selected_provider=start_provider(actor, CLONE_TASKS, targets),
        default_prompt=settings.web.clone_default_prompt,
        default_resolution=DEFAULT_RESOLUTION,
        resolutions=offered_resolutions(),
        max_images=settings.web.clone_max_images,
        max_links=settings.clone_links.max_links,
        live_poll_s=settings.web.gallery_live_poll_s,
        exports=exports().all(),
        default_export=settings.exports.default,
        default_mirror=settings.exports.mirror,
        drive_ready=bool(refresh_token(user_id)),
        # What is cached only: the page never waits on Google.
        drive_space=drive_space(user_id, ask=False),
        reel_mb=settings.clone_links.reel_mb,
    )
