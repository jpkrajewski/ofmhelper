# Copyright (C) 2026 jpkrajewski

from pathlib import Path
from typing import Self

from ofmhelpers.config import settings
from ofmhelpers.media.repurpose.pipelines.checker import ExecutionChecker
from ofmhelpers.media.repurpose.pipelines.executor import RepurposeExecutor
from ofmhelpers.media.repurpose.pipelines.models import (
    PipelineSetup,
    PipelineSpec,
    RepurposeResult,
    Stage,
)
from ofmhelpers.media.repurpose.pipelines.planner import ExecutionPlanner
from ofmhelpers.media.repurpose.pipelines.recorder import ExecutionRecorder
from ofmhelpers.media.repurpose.pipelines.run import PipelineRun
lazy from ofmhelpers.media.repurpose.pipelines.keeper import ExecutionKeeper
lazy from ofmhelpers.media.repurpose.transformations.video import VideoSettings


def pipeline_names() -> list[str]:
    return sorted(p.stem for p in Path(__file__).parent.glob("*.json"))


class Pipeline:
    def __init__(self, source: Path, output: Path) -> None:
        self.setup = PipelineSetup(
            source=source,
            output=output,
            timeout_s=settings.web.uniqify_encode_timeout_s,
        )
        self.keeper: ExecutionKeeper | None = None

    @property
    def spec(self) -> PipelineSpec:
        return self.setup.spec

    def keep_executions_with(self, keeper: ExecutionKeeper) -> Self:
        self.keeper = keeper
        return self

    @classmethod
    def paths(cls, source: str | Path, output: str | Path) -> Self:
        return cls(Path(source), Path(output))

    def seed(self, seed: int | None) -> Self:
        self.setup.first_seed = seed
        return self

    def load(self, name: str) -> Self:
        path = Path(__file__).with_name(f"{name}.json")
        if not path.is_file():
            msg = f"unknown pipeline {name!r}; known: {', '.join(pipeline_names())}"
            raise ValueError(msg)
        self.setup.spec = PipelineSpec.model_validate_json(
            path.read_text(encoding="utf-8"),
        )
        return self

    def stage(
        self,
        preset: str,
        *,
        max_attempts: int = 1,
        tries_per_attempt: int = 1,
        strength_increase: float = 0.0,
        video: VideoSettings | None = None,
    ) -> Self:
        self.spec.stages.append(
            Stage(
                preset=preset,
                max_attempts=max_attempts,
                tries_per_attempt=tries_per_attempt,
                strength_increase=strength_increase,
                video=video,
            ),
        )
        return self

    def return_on_unique(self) -> Self:
        self.spec.return_on_unique = True
        return self

    def device(self, name: str) -> Self:
        self.spec.device = name
        return self

    def video(self, knobs: VideoSettings) -> Self:
        self.setup.run_video = knobs
        return self

    def run(self) -> list[RepurposeResult]:
        planner = ExecutionPlanner(self.spec, settings.repurpose.max_tries)
        planner.refuse_over_the_cap()
        planner.refuse_audio_retries_on_a_recorder()
        executor = RepurposeExecutor(self.setup)
        run = PipelineRun(
            self.setup,
            planner=planner,
            executor=executor,
            checker=ExecutionChecker(self.setup),
            recorder=ExecutionRecorder(planner.planned_executions, self.keeper),
        )
        try:
            return [run.first_passing()]
        except Exception:
            executor.discard()
            self.setup.output.unlink(missing_ok=True)
            raise
