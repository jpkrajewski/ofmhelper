"""/clone's SOP: the fixed examples it shows (`clone_examples`)."""

from enum import StrEnum

from pydantic import BaseModel


class ExamplePart(StrEnum):
    """The files an example keeps in its own folder."""

    # The video that was cloned and its clone, next to each other, as one.
    VIDEO = "video"
    POSTER = "poster"  # that video's first frame


class CloneExample(BaseModel):
    """A `clone_examples` row: its title and the prompt it shows; `job_id`
    the run it was made from, while that run exists."""

    id: int
    title: str
    prompt: str
    position: int = 0
    job_id: str | None = None


class ExampleCard(BaseModel):
    """One example as the SOP dialog shows it."""

    id: int
    title: str
    prompt: str
    video_url: str
    poster_url: str
