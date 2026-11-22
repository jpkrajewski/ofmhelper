from itertools import repeat
from pathlib import Path
from typing import Self
lazy from collections.abc import Iterator

import logfire

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.media.repurpose.audio_check import BrokenAudioError, verify_audio
from ofmhelpers.media.repurpose.pipelines.models import (
    PipelineSpec,
    PlannedTry,
    RepurposeResult,
    Stage,
)
from ofmhelpers.media.repurpose.repurpose import Repurpose
lazy from ofmhelpers.media.repurpose.checker import check
lazy from ofmhelpers.media.repurpose.transformations.video import VideoSettings

logger = get_logger(__name__)


class StillLooksLikeSourceError(RuntimeError): ...


def pipeline_names() -> list[str]:
    return sorted(p.stem for p in Path(__file__).parent.glob("*.json"))


class Pipeline:
    def __init__(self, source: Path, output: Path) -> None:
        self.source = source
        self.output = output
        self.spec = PipelineSpec()
        self.first_seed: int | None = None
        self.run_video: VideoSettings | None = None
        self.timeout_s = settings.web.uniqify_encode_timeout_s

    @classmethod
    def paths(cls, source: str | Path, output: str | Path) -> Self:
        return cls(Path(source), Path(output))

    def seed(self, seed: int | None) -> Self:
        self.first_seed = seed
        return self

    def load(self, name: str) -> Self:
        path = Path(__file__).with_name(f"{name}.json")
        if not path.is_file():
            msg = f"unknown pipeline {name!r}; known: {', '.join(pipeline_names())}"
            raise ValueError(msg)
        self.spec = PipelineSpec.model_validate_json(path.read_text(encoding="utf-8"))
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
            )
        )
        return self

    def return_on_unique(self) -> Self:
        self.spec.return_on_unique = True
        return self

    def device(self, name: str) -> Self:
        self.spec.device = name
        return self

    def video(self, knobs: VideoSettings) -> Self:
        self.run_video = knobs
        return self

    def run(self) -> list[RepurposeResult]:
        self._refuse_over_the_cap()
        try:
            return [self._first_passing()]
        except BaseException:
            self.output.unlink(missing_ok=True)
            raise

    def _refuse_over_the_cap(self) -> None:
        cap = settings.repurpose.max_tries
        if not 1 <= self.spec.attempts <= cap:
            msg = (
                f"a pipeline has 1 to {cap} attempts in all (OFM_REPURPOSE_MAX_TRIES),"
                f" this one has {self.spec.attempts}"
            )
            raise ValueError(msg)

    def _tries(self) -> Iterator[PlannedTry]:
        for stage in self.spec.stages:
            for failed in range(stage.max_attempts):
                strength = round(1 + stage.strength_increase * failed, 4)
                planned = PlannedTry(stage=stage, strength=strength)
                yield from repeat(planned, stage.tries_per_attempt)

    def _first_passing(self) -> RepurposeResult:
        tries = list(self._tries())
        for number, planned in enumerate(tries, start=1):
            with logfire.span(
                "repurpose pipeline {preset} try {number} of {total}",
                preset=planned.stage.preset,
                number=number,
                total=len(tries),
                strength=planned.strength,
            ):
                made = self._made(planned, number)
            if made is not None:
                return made
        msg = f"Still matches the source after {len(tries)} tries"
        raise StillLooksLikeSourceError(msg)

    def _maker(self, planned: PlannedTry, number: int) -> Repurpose:
        seed = None if self.first_seed is None else self.first_seed + number - 1
        maker = Repurpose(
            self.source, self.output, seed=seed, timeout_s=self.timeout_s
        ).apply_json_preset(planned.stage.preset)
        if self.spec.device:
            maker.apply_device_preset(self.spec.device)
        maker.video = maker.video.merged(planned.stage.video).merged(self.run_video)
        if planned.strength != 1:
            maker.stronger(planned.strength)
        return maker

    def _made(self, planned: PlannedTry, number: int) -> RepurposeResult | None:
        ran = self._maker(planned, number).run()
        if not self._audio_passes(planned, number):
            return None
        report = (
            check(self.source, self.output, timeout_s=self.timeout_s)
            if self.spec.return_on_unique
            else None
        )
        if report is not None and not report.unique:
            logger.warning(
                "repurpose pipeline: try %s (%s at strength %s) still matches its source: %s",
                number,
                planned.stage.preset,
                planned.strength,
                report.model_dump_json(),
            )
            return None
        return RepurposeResult(
            name=self.output.name,
            path=str(self.output),
            report=report,
            step=ran.step,
            stage=planned.stage,
            attempts=number,
            strength=planned.strength,
            seconds=ran.seconds,
        )

    def _audio_passes(self, planned: PlannedTry, number: int) -> bool:
        try:
            verify_audio(self.source, self.output)
        except BrokenAudioError:
            logger.warning(
                "repurpose pipeline: try %s (%s at strength %s) broke the audio",
                number,
                planned.stage.preset,
                planned.strength,
                exc_info=True,
            )
            return False
        return True
