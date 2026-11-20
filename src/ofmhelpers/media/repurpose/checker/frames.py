import re
import tempfile
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict

from ofmhelpers.media.ffmpeg import binary, run_log

_MAX_SIDE = 320
_STEP_SHARE = 0.99
_PTS = re.compile(r"pts_time:\s*(-?[0-9.]+)")


class Sampled(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

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
        images = [
            Image.open(p).convert("RGB") for p in sorted(Path(tmp).glob("f_*.png"))
        ]
    count = min(len(times), len(images))
    first = times[0] if times else 0.0
    return Sampled(
        times=[round(t - first, 3) for t in times[:count]], images=images[:count]
    )
