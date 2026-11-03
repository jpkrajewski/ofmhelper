"""/pictures: a preset (a prompt + reference images) as the page shows it."""

from pydantic import BaseModel


class PicturePreset(BaseModel):
    """`images`: asset-store paths, in the order a run sends them.
    `aspect_ratio`: every run's output shape ("3:4"), None = the picture's
    own. `mine`: the viewer owns it (may edit and delete it); else it is the
    agency's, shared with everyone."""

    id: int
    owner_id: int
    name: str
    prompt: str
    images: list[str]
    aspect_ratio: str | None = None
    mine: bool = False
