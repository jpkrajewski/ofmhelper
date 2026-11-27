# Copyright (C) 2026 jpkrajewski

from ofmhelpers.media.repurpose.audio_swap import AudioSwap
from ofmhelpers.media.repurpose.repurpose import Repurpose
lazy from ofmhelpers.media.repurpose.pipelines.models import PipelineSetup, PlannedTry
lazy from ofmhelpers.media.repurpose.repurpose import RunResult
lazy from ofmhelpers.media.repurpose.transformations.audio import AudioSettings


class RepurposeExecutor:
    def __init__(self, setup: PipelineSetup) -> None:
        self.setup = setup
        self.swap: AudioSwap | None = None

    def picture(self, planned: PlannedTry, number: int) -> RunResult:
        setup = self.setup
        maker = Repurpose(
            setup.source,
            setup.output,
            seed=self._seed(number),
            timeout_s=setup.timeout_s,
        ).apply_json_preset(planned.stage.preset)
        if setup.spec.device:
            maker.apply_device_preset(setup.spec.device)
        maker.video = maker.video.merged(planned.stage.video).merged(setup.run_video)
        if planned.strength != 1:
            maker.stronger(planned.strength)
        self.discard()
        self.swap = None
        ran = maker.run()
        if ran.step is not None and maker.device.recorder is None:
            self.swap = AudioSwap(maker, ran.step)
        return ran

    def new_audio(self, audio: AudioSettings, number: int) -> RunResult:
        return self._approved_picture().redo(audio, self._seed(number))

    def keep_new_sound(self) -> None:
        self._approved_picture().keep_new_sound()

    def restore_first_sound(self) -> None:
        self._approved_picture().restore_first_sound()

    def discard(self) -> None:
        if self.swap is not None:
            self.swap.discard()

    def _approved_picture(self) -> AudioSwap:
        if self.swap is None:
            msg = "no picture from a plain encode yet: no audio-only redo"
            raise ValueError(msg)
        return self.swap

    def _seed(self, number: int) -> int | None:
        first_seed = self.setup.first_seed
        return None if first_seed is None else first_seed + number - 1
