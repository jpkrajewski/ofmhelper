"""A video's frames, sampled evenly in time, as small RGB images with their
times: what the hashes read. Every hash shrinks a frame to 8-32 px anyway.

A frame is the first one at or after each step (`select` on timestamps), so
a copy sampled at 1/s and its source at 10/s pick the same moments. ffmpeg's
`fps` filter picks somewhere inside each slot: a plain re-encode then landed
0.1-0.4 s off its source and fast handheld footage read 34-104 PDQ bits away
(2026-10-26).
"""

import re
import tempfile
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict

from ofmhelpers.repurpose.runner import run_log
from ofmhelpers.utils.ffmpeg import binary

# Frames fit in this square before hashing: plenty for 8x8..32x32 hashes.
_MAX_SIDE = 320
# A step a hair short of 1/fps: a 30 fps frame exactly on the step counts.
_STEP_SHARE = 0.99
_PTS = re.compile(r"pts_time:\s*(-?[0-9.]+)")


class Sampled(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Seconds from the first sampled frame.
    times: list[float]
    images: list[Image.Image]


def sample(path: Path, fps: float, timeout_s: int) -> Sampled:
    step = _STEP_SHARE / fps
    with tempfile.TemporaryDirectory(prefix="checker_") as tmp:
        log = run_log(
            [
                binary("ffmpeg"),
                "-v",
                "info",
                "-i",
                str(path),
                "-vf",
                (
                    f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{step:.4f})',"
                    f"showinfo,scale={_MAX_SIDE}:{_MAX_SIDE}"
                    ":force_original_aspect_ratio=decrease"
                ),
                "-fps_mode",
                "vfr",
                str(Path(tmp) / "f_%05d.png"),
            ],
            timeout_s,
        )
        times = [float(t) for t in _PTS.findall(log)]
        # convert() loads the pixels before the folder goes away.
        images = [
            Image.open(p).convert("RGB") for p in sorted(Path(tmp).glob("f_*.png"))
        ]
    count = min(len(times), len(images))
    first = times[0] if times else 0.0
    return Sampled(
        times=[round(t - first, 3) for t in times[:count]], images=images[:count]
    )
