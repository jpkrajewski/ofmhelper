# Copyright (C) 2026 jpkrajewski

import logging
import time

from ofmhelpers.media.repurpose.pipelines.models import (
    ExecutionKind,
    RepurposeResult,
    Verdict,
)
lazy from ofmhelpers.media.repurpose.pipelines.checker import ExecutionChecker
lazy from ofmhelpers.media.repurpose.pipelines.executor import RepurposeExecutor
lazy from ofmhelpers.media.repurpose.pipelines.models import (
    AudioRetry,
    PipelineSetup,
    PlannedTry,
)
lazy from ofmhelpers.media.repurpose.pipelines.planner import ExecutionPlanner
lazy from ofmhelpers.media.repurpose.pipelines.recorder import ExecutionRecorder
lazy from ofmhelpers.media.repurpose.repurpose import RunResult

logger = logging.getLogger(__name__)


class PipelineRun:
    def __init__(
        self,
        setup: PipelineSetup,
        *,
        planner: ExecutionPlanner,
        executor: RepurposeExecutor,
        checker: ExecutionChecker,
        recorder: ExecutionRecorder,
    ) -> None:
        self.setup = setup
        self.planner = planner
        self.executor = executor
        self.checker = checker
        self.recorder = recorder

    def first_passing(self) -> RepurposeResult:
        for planned in self.planner.picture_tries():
            if not self._budget_left():
                break
            picture = self._picture(planned)
            verdict = self.recorder.last.verdict
            if verdict.passed:
                return self._result(planned, picture, picture.seconds, verdict)
            if verdict.picture_passed and self.planner.retries_audio:
                return self._with_new_audio(planned, picture)
        raise self.recorder.failure()

    def _budget_left(self) -> bool:
        return self.planner.budget_left(len(self.recorder.executions))

    def _picture(self, planned: PlannedTry) -> RunResult:
        started = time.monotonic()
        with self.recorder.span(ExecutionKind.PICTURE, planned) as span:
            ran = self.executor.picture(planned, self.recorder.next_number)
            self.recorder.record(
                ExecutionKind.PICTURE,
                planned,
                self.checker.picture(),
                time.monotonic() - started,
                span,
            )
        return ran

    def _with_new_audio(
        self,
        planned: PlannedTry,
        picture: RunResult,
    ) -> RepurposeResult:
        approved_picture = self.recorder.last.verdict
        encode_seconds = picture.seconds
        for retry in self.planner.audio_retries():
            if not self._budget_left():
                break
            ran = self._new_audio(planned, retry, approved_picture)
            if ran is None:
                continue
            encode_seconds += ran.seconds
            heard = self.recorder.last.verdict
            if heard.passed:
                self.executor.keep_new_sound()
                return self._result(planned, ran, encode_seconds, heard)
        if self.setup.spec.keep_first_sound and approved_picture.broken_audio is None:
            self.executor.restore_first_sound()
            self.recorder.first_sound_kept(self.setup.output.name)
            return self._result(planned, picture, encode_seconds, approved_picture)
        raise self.recorder.failure()

    def _new_audio(
        self,
        planned: PlannedTry,
        retry: AudioRetry,
        approved_picture: Verdict,
    ) -> RunResult | None:
        started = time.monotonic()
        with self.recorder.span(ExecutionKind.AUDIO, planned) as span:
            try:
                ran = self.executor.new_audio(retry.audio, self.recorder.next_number)
            except RuntimeError as failed:
                logger.warning(
                    "repurpose pipeline: the audio redo of %s failed",
                    self.setup.output.name,
                    exc_info=True,
                )
                self.recorder.record(
                    ExecutionKind.AUDIO,
                    planned,
                    Verdict(
                        report=approved_picture.report,
                        broken_audio=f"audio redo failed: {failed}",
                    ),
                    time.monotonic() - started,
                    span,
                )
                return None
            self.recorder.record(
                ExecutionKind.AUDIO,
                planned,
                self.checker.new_audio(approved_picture),
                time.monotonic() - started,
                span,
            )
        return ran

    def _result(
        self,
        planned: PlannedTry,
        delivered: RunResult,
        encode_seconds: float,
        verdict: Verdict,
    ) -> RepurposeResult:
        return RepurposeResult(
            name=self.setup.output.name,
            path=str(self.setup.output),
            report=verdict.report,
            step=delivered.step,
            stage=planned.stage,
            attempts=self.recorder.last.number,
            strength=planned.strength,
            seconds=encode_seconds,
            executions=self.recorder.executions,
            kept_first_sound=not verdict.audio_passed,
        )
