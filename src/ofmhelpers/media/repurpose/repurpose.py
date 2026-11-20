import random
import tempfile
import time
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo
lazy import datetime

import logfire
from pydantic import BaseModel

from ofmhelpers.media.ffmpeg import run
from ofmhelpers.media.recorder import load_profile
from ofmhelpers.media.recorder.models import Recording
from ofmhelpers.media.recorder.recorder import record
from ofmhelpers.media.repurpose import command, container
from ofmhelpers.media.repurpose.command import EncodeDraws
from ofmhelpers.media.repurpose.devices import Device, Recorder, load_device
from ofmhelpers.media.repurpose.level import makeup_db
from ofmhelpers.media.repurpose.presets import load_preset
from ofmhelpers.media.repurpose.probe import Source, probe
from ofmhelpers.media.repurpose.transformations.audio import (
    AudioDraws,
    AudioSettings,
    AudioTransformer,
)
from ofmhelpers.media.repurpose.transformations.video import (
    VideoDraws,
    VideoSettings,
    VideoTransformer,
)

_DEFAULT_DEVICE = "strip"
_MASTER_DEVICE = "master"
_DEFAULT_TIMEOUT_S = 600


class EncodeStep(BaseModel):
    command: list[str]
    video: VideoDraws
    audio: AudioDraws
    encode: EncodeDraws
    makeup_db: float


class _Recorded(BaseModel):
    step: EncodeStep | None
    recording: Recording


class RunResult(BaseModel):
    output: Path
    source: Source
    device: str
    step: EncodeStep | None
    recording: Recording | None
    seconds: float


class Repurpose:
    def __init__(
        self,
        src: str | Path,
        dst: str | Path,
        *,
        seed: int | None = None,
        recorded_at: datetime.datetime | None = None,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.src = Path(src)
        self.dst = Path(dst)
        self.seed = seed
        self.recorded_at = recorded_at
        self.timeout_s = timeout_s
        self.video = VideoSettings()
        self.audio = AudioSettings()
        self.device_name = _DEFAULT_DEVICE
        self.device: Device = load_device(_DEFAULT_DEVICE)

    def apply_video_preset(self, name: str, *, special: bool = False) -> Self:
        preset = load_preset(name)
        self.video = preset.video.merged(preset.video_special if special else None)
        return self

    def apply_audio_preset(self, name: str, *, special: bool = False) -> Self:
        preset = load_preset(name)
        self.audio = preset.audio.merged(preset.audio_special if special else None)
        return self

    def apply_json_preset(self, name: str, *, special: bool = False) -> Self:
        preset = load_preset(name)
        self.apply_video_preset(name, special=special)
        self.apply_audio_preset(name, special=special)
        device = (
            preset.device_special
            if special and preset.device_special
            else preset.device
        )
        if device:
            self.apply_device_preset(device)
        return self

    def apply_device_preset(self, name: str) -> Self:
        self.device = load_device(name)
        self.device_name = name
        return self

    def run(self) -> RunResult:
        started = time.monotonic()
        rng = random.Random(self.seed)  # noqa: S311 - jitter, not cryptography
        source = probe(self.src, self.timeout_s)
        self.dst.parent.mkdir(parents=True, exist_ok=True)
        recorder = self.device.recorder
        step: EncodeStep | None
        recording: Recording | None
        if recorder is None:
            step = self._encode(source, self.device, self.dst, rng)
            recording = None
        else:
            recorded = self._record(source, recorder, rng)
            step, recording = recorded.step, recorded.recording
        return RunResult(
            output=self.dst,
            source=source,
            device=self.device_name,
            step=step,
            recording=recording,
            seconds=round(time.monotonic() - started, 2),
        )

    def _encode(
        self, source: Source, device: Device, dst: Path, rng: random.Random
    ) -> EncodeStep:
        video = VideoTransformer(self.video, source, device.output_size(source), rng)
        audio = AudioTransformer(
            self.audio, video.timeline.speed, device.audio.sample_rate, rng
        )
        encode = command.draw_encode(device, source, rng, self.recorded_at)
        makeup = (
            makeup_db(self.src, video, audio.filters(), self.timeout_s)
            if self.audio.match_level and source.has_audio
            else 0.0
        )
        with tempfile.TemporaryDirectory(prefix="repurpose_") as work:
            argv = command.build(
                src=self.src,
                dst=dst,
                source=source,
                video=video,
                audio=audio,
                device=device,
                encode=encode,
                makeup_db=makeup,
                work_dir=Path(work),
            )
            run(argv, self.timeout_s)
        container.patch(
            dst,
            device.patches.rename_keys,
            blank_compressor=device.patches.blank_compressor_name,
        )
        return EncodeStep(
            command=argv,
            video=video.draws,
            audio=audio.draws,
            encode=encode,
            makeup_db=makeup,
        )

    def _record(
        self, source: Source, recorder: Recorder, rng: random.Random
    ) -> _Recorded:
        edited = self.video != VideoSettings() or self.audio != AudioSettings()
        recording = self._recording(recorder, rng)
        with tempfile.TemporaryDirectory(prefix="repurpose_rec_") as work:
            step = None
            recorder_input = self.src
            if edited:
                recorder_input = Path(work) / "master.mp4"
                master = load_device(_MASTER_DEVICE)
                with logfire.span("repurpose master encode"):
                    step = self._encode(source, master, recorder_input, rng)
            with logfire.span("repurpose record {profile}", profile=recorder.profile):
                record(
                    recorder_input,
                    self.dst,
                    load_profile(recorder.profile),
                    recording,
                    self.timeout_s,
                )
        return _Recorded(step=step, recording=recording)

    def _recording(self, recorder: Recorder, rng: random.Random) -> Recording:
        seed = rng.getrandbits(32)
        if self.recorded_at is None:
            made = Recording.ending_now(
                ZoneInfo(recorder.time_zone), recorder.place, rng
            )
            return made.model_copy(update={"seed": seed})
        at = recorder.place.sample(rng) if recorder.place else None
        return Recording(
            end=self.recorded_at,
            duration_s=0,
            location=at.point if at else None,
            location_accuracy_m=at.accuracy_m if at else None,
            seed=seed,
        )
