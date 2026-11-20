from enum import StrEnum

from pydantic import BaseModel, Field

from ofmhelpers.schemas.gallery import TaskCard
from ofmhelpers.schemas.persistence import ResultFile
lazy from ofmhelpers.schemas.drive import DriveUpload


class Medium(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


class MethodScore(BaseModel, frozen=True):
    method: str
    medium: Medium
    percent: int


class CopyScore(BaseModel, frozen=True):
    methods: list[MethodScore] = []
    looks_like_source: bool
    sounds_like_source: bool | None

    @property
    def unique(self) -> bool:
        return not self.looks_like_source and not self.sounds_like_source


class RepurposeResult(ResultFile):
    score: CopyScore | None = None
    attempts: int = 1


class RepurposeCard(TaskCard):
    score: CopyScore | None = None
    attempts: int = 1
    drive_uploads: list[DriveUpload] = Field(default_factory=list)
