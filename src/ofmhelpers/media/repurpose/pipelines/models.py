# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel, Field

from ofmhelpers.media.repurpose.transformations.video import VideoSettings
lazy from ofmhelpers.media.repurpose.checker.schema import CheckReport
lazy from ofmhelpers.media.repurpose.repurpose import EncodeStep


class Stage(BaseModel, frozen=True, extra="forbid"):
    preset: str
    max_attempts: int = Field(default=1, ge=1)
    tries_per_attempt: int = Field(default=1, ge=1)
    strength_increase: float = Field(default=0.0, ge=0, le=0.1)
    video: VideoSettings | None = None


class PipelineSpec(BaseModel, extra="forbid"):
    stages: list[Stage] = Field(default_factory=list)
    device: str | None = None
    return_on_unique: bool = False

    @property
    def attempts(self) -> int:
        return sum(stage.max_attempts for stage in self.stages)


class PlannedTry(BaseModel, frozen=True):
    stage: Stage
    strength: float


class RepurposeResult(BaseModel):
    name: str
    path: str
    report: CheckReport | None = None
    step: EncodeStep | None = None
    stage: Stage | None = None
    attempts: int = 1
    strength: float = 1.0
    seconds: float = 0.0
