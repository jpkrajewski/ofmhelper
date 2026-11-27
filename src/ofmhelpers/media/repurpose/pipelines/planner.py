# Copyright (C) 2026 jpkrajewski

lazy from collections.abc import Iterator

from ofmhelpers.media.repurpose.devices import load_device
from ofmhelpers.media.repurpose.pipelines.models import (
    AudioRetry,
    PipelineSpec,
    PlannedTry,
)
from ofmhelpers.media.repurpose.presets import load_preset


class ExecutionPlanner:
    def __init__(self, spec: PipelineSpec, max_executions: int) -> None:
        self.spec = spec
        self.max_executions = max_executions

    @property
    def planned_executions(self) -> int:
        return self.spec.executions

    @property
    def retries_audio(self) -> bool:
        return bool(self.spec.audio_retries)

    def refuse_over_the_cap(self) -> None:
        if not 1 <= self.spec.executions <= self.max_executions:
            msg = (
                f"a pipeline runs ffmpeg 1 to {self.max_executions} times in all "
                f"(OFM_REPURPOSE_MAX_TRIES), this one up to {self.spec.executions}: "
                f"{self.spec.picture_executions} picture tries and "
                f"{self.spec.audio_executions} audio retries"
            )
            raise ValueError(msg)

    def refuse_audio_retries_on_a_recorder(self) -> None:
        if not self.spec.audio_retries:
            return
        for stage in self.spec.stages:
            device_name = self.spec.device or load_preset(stage.preset).device
            if device_name is None:
                continue
            if load_device(device_name).recorder is not None:
                msg = (
                    f"device {device_name!r} records its copy: a pipeline with "
                    "audio_retries needs a plain-encode device"
                )
                raise ValueError(msg)

    def budget_left(self, executed: int) -> bool:
        return executed < self.max_executions

    def picture_tries(self) -> Iterator[PlannedTry]:
        for stage in self.spec.stages:
            for failed in range(stage.max_attempts):
                strength = round(1 + stage.strength_increase * failed, 4)
                planned = PlannedTry(stage=stage, strength=strength)
                for _ in range(stage.tries_per_attempt):
                    yield planned

    def audio_retries(self) -> Iterator[AudioRetry]:
        for retry in self.spec.audio_retries:
            for _ in range(retry.tries):
                yield retry
