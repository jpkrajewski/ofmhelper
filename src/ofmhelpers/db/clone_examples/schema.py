from enum import StrEnum

from pydantic import BaseModel


class ExamplePart(StrEnum):
    VIDEO = "video"
    POSTER = "poster"


class CloneExample(BaseModel):
    id: int
    title: str
    prompt: str
    position: int = 0
    job_id: str | None = None


class ExampleCard(BaseModel):
    id: int
    title: str
    prompt: str
    video_url: str
    poster_url: str
