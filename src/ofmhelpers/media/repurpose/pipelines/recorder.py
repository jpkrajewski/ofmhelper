# Copyright (C) 2026 jpkrajewski

import logging

import logfire

from ofmhelpers.media.repurpose.pipelines.errors import (
    StillLooksLikeSourceError,
    StillSoundsLikeSourceError,
)
from ofmhelpers.media.repurpose.pipelines.models import Execution
lazy from ofmhelpers.media.repurpose.pipelines.keeper import ExecutionKeeper
lazy from ofmhelpers.media.repurpose.pipelines.models import (
    ExecutionKind,
    PlannedTry,
    Verdict,
)

logger = logging.getLogger(__name__)


class ExecutionRecorder:
    def __init__(
        self,
        planned_executions: int,
        keeper: ExecutionKeeper | None,
    ) -> None:
        self.planned_executions = planned_executions
        self.keeper = keeper
        self.executions: list[Execution] = []

    @property
    def next_number(self) -> int:
        return len(self.executions) + 1

    @property
    def last(self) -> Execution:
        return self.executions[-1]

    def span(self, kind: ExecutionKind, planned: PlannedTry) -> logfire.LogfireSpan:
        return logfire.span(
            "repurpose pipeline {kind} {preset} execution {number} of {total}",
            kind=kind.value,
            preset=planned.stage.preset,
            number=self.next_number,
            total=self.planned_executions,
            strength=planned.strength,
        )

    def record(
        self,
        kind: ExecutionKind,
        planned: PlannedTry,
        verdict: Verdict,
        seconds: float,
        span: logfire.LogfireSpan,
    ) -> None:
        execution = Execution(
            number=self.next_number,
            kind=kind,
            preset=planned.stage.preset,
            strength=planned.strength,
            seconds=round(seconds, 2),
            verdict=verdict,
        )
        self.executions.append(execution)
        report = verdict.report
        sound = report.audio if report is not None else None
        span.set_attributes(
            {
                "picture_passed": verdict.picture_passed,
                "audio_passed": verdict.audio_passed,
                "seconds": execution.seconds,
                "report": report.model_dump_json() if report is not None else "",
            },
        )
        logger.log(
            logging.INFO if verdict.passed else logging.WARNING,
            "repurpose pipeline: execution %s (%s, %s at strength %s, %s s): "
            "picture passed %s, sound passed %s, sound similarity %s, "
            "sound windows matched %s, broken audio %s",
            execution.number,
            execution.kind.value,
            execution.preset,
            execution.strength,
            execution.seconds,
            verdict.picture_passed,
            verdict.audio_passed,
            sound.similarity if sound is not None else None,
            sound.matched_share if sound is not None else None,
            verdict.broken_audio,
            extra={"execution": execution.model_dump(mode="json")},
        )
        if self.keeper is not None:
            self.keeper.keep(execution)

    def first_sound_kept(self, copy_name: str) -> None:
        logger.warning(
            "repurpose pipeline: %s still sounds like its source after %s "
            "executions, the picture's first sound is kept",
            copy_name,
            len(self.executions),
        )

    def failure(self) -> StillLooksLikeSourceError:
        count = len(self.executions)
        verdict = self.last.verdict
        if not verdict.picture_passed:
            msg = f"Picture still matches the source after {count} tries"
            return StillLooksLikeSourceError(msg)
        if verdict.broken_audio is not None:
            msg = f"Sound came out broken after {count} tries: {verdict.broken_audio}"
            return StillSoundsLikeSourceError(msg)
        msg = f"Sound still matches the source after {count} tries"
        return StillSoundsLikeSourceError(msg)
