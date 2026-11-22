import random

from ofmhelpers.media.repurpose import command
from ofmhelpers.media.repurpose.devices import load_device
from ofmhelpers.media.repurpose.presets import load_preset
from ofmhelpers.media.repurpose.probe import Source
from ofmhelpers.media.repurpose.repurpose import EncodeStep
from ofmhelpers.media.repurpose.transformations.audio import AudioTransformer
from ofmhelpers.media.repurpose.transformations.video import VideoTransformer

SOURCE = Source(duration=11.0, width=2160, height=3840, has_audio=True)


def drawn_step(seed: int) -> EncodeStep:
    preset = load_preset("spoof")
    assert preset.device is not None
    device = load_device(preset.device)
    rng = random.Random(seed)  # noqa: S311 - seeded draws, not cryptography
    video = VideoTransformer(preset.video, SOURCE, device.output_size(SOURCE), rng)
    audio = AudioTransformer(
        preset.audio, video.timeline.speed, device.audio.sample_rate, rng
    )
    return EncodeStep(
        command=[],
        video=video.draws,
        audio=audio.draws,
        encode=command.draw_encode(device, SOURCE, rng, None),
        makeup_db=0.0,
    )
