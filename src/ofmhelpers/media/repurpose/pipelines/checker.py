# Copyright (C) 2026 jpkrajewski

import logging

from ofmhelpers.media.repurpose.audio_check import (
    BrokenAudioError,
    levels,
    verify_audio,
)
from ofmhelpers.media.repurpose.pipelines.models import Verdict
lazy from ofmhelpers.media.repurpose.checker import check, check_sound_again
lazy from ofmhelpers.media.repurpose.pipelines.models import PipelineSetup

logger = logging.getLogger(__name__)


class ExecutionChecker:
    def __init__(self, setup: PipelineSetup) -> None:
        self.setup = setup
        self.source_levels = levels(setup.source)

    def picture(self) -> Verdict:
        setup = self.setup
        broken = self._broken_audio()
        if not setup.spec.return_on_unique:
            return Verdict(broken_audio=broken)
        report = check(setup.source, setup.output, timeout_s=setup.timeout_s)
        return Verdict(report=report, broken_audio=broken)

    def new_audio(self, approved_picture: Verdict) -> Verdict:
        broken = self._broken_audio()
        report = approved_picture.report
        if report is None or broken is not None:
            return Verdict(report=report, broken_audio=broken)
        heard = check_sound_again(report, timeout_s=self.setup.timeout_s)
        return Verdict(report=heard)

    def _broken_audio(self) -> str | None:
        try:
            verify_audio(self.source_levels, self.setup.output)
        except BrokenAudioError as broken:
            logger.warning(
                "repurpose pipeline: %s came out with broken audio",
                self.setup.output.name,
                exc_info=True,
            )
            return str(broken)
        return None
