# Copyright (C) 2026 jpkrajewski

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.media.repurpose.checker.schema import CheckReport
from ofmhelpers.media.repurpose.transformations.audio import AudioSettings
from ofmhelpers.media.repurpose.transformations.video import VideoSettings
lazy from ofmhelpers.media.repurpose.repurpose import EncodeStep


class Stage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    preset: str
    max_attempts: int = Field(default=1, ge=1)
    tries_per_attempt: int = Field(default=1, ge=1)
    strength_increase: float = Field(default=0.0, ge=0, le=0.1)
    video: VideoSettings | None = None


class AudioRetry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    audio: AudioSettings
    tries: int = Field(default=1, ge=1)


class PipelineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[Stage] = Field(default_factory=list)
    device: str | None = None
    return_on_unique: bool = False
    audio_retries: list[AudioRetry] = Field(default_factory=list)
    keep_first_sound: bool = False

    @property
    def picture_executions(self) -> int:
        total = 0
        for stage in self.stages:
            total += stage.max_attempts * stage.tries_per_attempt
        return total

    @property
    def audio_executions(self) -> int:
        total = 0
        for retry in self.audio_retries:
            total += retry.tries
        return total

    @property
    def executions(self) -> int:
        return self.picture_executions + self.audio_executions


class PipelineSetup(BaseModel):
    source: Path
    output: Path
    timeout_s: int
    spec: PipelineSpec = Field(default_factory=PipelineSpec)
    first_seed: int | None = None
    run_video: VideoSettings | None = None


class PlannedTry(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: Stage
    strength: float


class ExecutionKind(StrEnum):
    PICTURE = "picture"
    AUDIO = "audio"


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    report: CheckReport | None = None
    broken_audio: str | None = None

    @property
    def picture_passed(self) -> bool:
        return self.report is None or not self.report.looks_like_source

    @property
    def audio_passed(self) -> bool:
        if self.broken_audio is not None:
            return False
        return self.report is None or not self.report.sounds_like_source

    @property
    def passed(self) -> bool:
        return self.picture_passed and self.audio_passed


class Execution(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int
    kind: ExecutionKind
    preset: str
    strength: float
    seconds: float
    verdict: Verdict


class RepurposeResult(BaseModel):
    name: str
    path: str
    report: CheckReport | None = None
    step: EncodeStep | None = None
    stage: Stage | None = None
    attempts: int = 1
    strength: float = 1.0
    seconds: float = 0.0
    executions: list[Execution] = Field(default_factory=list)
    kept_first_sound: bool = False

    @property
    def audio_tries(self) -> int:
        count = 0
        for execution in self.executions:
            if execution.kind == ExecutionKind.AUDIO:
                count += 1
        return count

    @property
    def picture_tries(self) -> int:
        return self.attempts - self.audio_tries
