"""Audio level of the trimmed source, as is or through a filter chain: what
`match_level` needs to give a copy its source's loudness back."""

import re
from pathlib import Path

from ofmhelpers.repurpose.command import input_args
from ofmhelpers.repurpose.runner import run_log
from ofmhelpers.repurpose.transformations.video import VideoTransformer
from ofmhelpers.utils.ffmpeg import binary

_MEAN_VOLUME = re.compile(r"mean_volume: (-?[\d.]+) dB")
# The make-up gain stays under this (dB); the limiter catches the peaks.
_MAKEUP_MAX_DB = 30.0


def mean_volume_db(
    src: Path, video: VideoTransformer, af: str, timeout_s: int
) -> float:
    log = run_log(
        [
            binary("ffmpeg"),
            "-nostats",
            "-loglevel",
            "info",
            *input_args(src, video),
            "-vn",
            "-af",
            f"{af},volumedetect",
            "-f",
            "null",
            "-",
        ],
        timeout_s,
    )
    match = _MEAN_VOLUME.search(log)
    if match is None:
        msg = "volumedetect printed no mean volume"
        raise RuntimeError(msg)
    return float(match.group(1))


def makeup_db(src: Path, video: VideoTransformer, af: str, timeout_s: int) -> float:
    """Gain back to the source's level: never a cut, at most 30 dB."""
    source = mean_volume_db(src, video, "anull", timeout_s)
    processed = mean_volume_db(src, video, af, timeout_s)
    return round(min(_MAKEUP_MAX_DB, max(0.0, source - processed)), 2)
