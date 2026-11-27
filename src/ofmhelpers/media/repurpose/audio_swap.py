# Copyright (C) 2026 jpkrajewski

import time

from ofmhelpers.media.ffmpeg import binary, run
from ofmhelpers.media.repurpose import command, container
from ofmhelpers.media.repurpose.level import makeup_db
from ofmhelpers.media.repurpose.probe import probe
from ofmhelpers.media.repurpose.repurpose import EncodeStep, RunResult
from ofmhelpers.media.repurpose.transformations.audio import AudioTransformer
lazy from ofmhelpers.media.repurpose.repurpose import Repurpose
lazy from ofmhelpers.media.repurpose.transformations.audio import AudioSettings


class AudioSwap:
    def __init__(self, maker: Repurpose, picture: EncodeStep) -> None:
        if maker.device.recorder is not None:
            msg = f"device {maker.device_name!r} records its copy: no audio-only redo"
            raise ValueError(msg)
        self.maker = maker
        self.picture = picture
        self.first_audio = maker.audio
        self.copy = maker.dst
        self.approved = maker.dst.with_name(
            f"{maker.dst.stem}.picture{maker.dst.suffix}",
        )
        self.approved.unlink(missing_ok=True)
        self.set_aside = False

    def redo(self, audio: AudioSettings, seed: int | None) -> RunResult:
        started = time.monotonic()
        maker = self.maker
        source = probe(maker.src, maker.timeout_s)
        if not source.has_audio:
            msg = f"{maker.src.name} has no audio: no audio-only redo"
            raise ValueError(msg)
        if not self.set_aside:
            self.copy.replace(self.approved)
            self.set_aside = True
        maker.seed = seed
        settings = self.first_audio.merged(audio)
        timeline = self.picture.video.timeline
        transformer = AudioTransformer(
            settings,
            timeline.speed,
            maker.device.audio.sample_rate,
            maker.seeded_random(),
        )
        makeup = 0.0
        if settings.match_level:
            makeup = makeup_db(
                maker.src,
                timeline,
                transformer.filters(),
                maker.timeout_s,
            )
        argv = self._command(transformer, makeup)
        run(argv, maker.timeout_s)
        container.patch(
            self.copy,
            maker.device.patches.rename_keys,
            blank_compressor=maker.device.patches.blank_compressor_name,
        )
        step = EncodeStep(
            command=argv,
            video=self.picture.video,
            audio=transformer.draws,
            encode=self.picture.encode,
            makeup_db=makeup,
        )
        return RunResult(
            output=self.copy,
            source=source,
            device=maker.device_name,
            step=step,
            recording=None,
            seconds=round(time.monotonic() - started, 2),
        )

    def keep_new_sound(self) -> None:
        self.approved.unlink(missing_ok=True)
        self.set_aside = False

    def restore_first_sound(self) -> None:
        if self.set_aside:
            self.approved.replace(self.copy)
            self.set_aside = False

    def discard(self) -> None:
        self.approved.unlink(missing_ok=True)
        self.set_aside = False

    def _command(self, transformer: AudioTransformer, makeup: float) -> list[str]:
        device = self.maker.device
        encode = self.picture.encode
        args = [binary("ffmpeg"), "-y", "-loglevel", "error", "-i", str(self.approved)]
        args += command.input_args(self.maker.src, self.picture.video.timeline)
        args += ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy"]
        args += ["-filter:a", transformer.filters(makeup)]
        args += command.audio_encode_args(device, encode)
        if device.video.tag:
            args += ["-tag:v", device.video.tag]
        if device.video.timescale:
            args += ["-video_track_timescale", str(device.video.timescale)]
        args += command.container_args(device, encode, has_audio=True)
        return [*args, str(self.copy)]
