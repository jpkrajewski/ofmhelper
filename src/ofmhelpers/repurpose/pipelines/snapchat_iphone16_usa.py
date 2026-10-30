"""The snapchat-US pipeline: stealth_wild-like picture (gentler: no vignette,
brighter bias, 0.1 s head trim) + a lowered Duplyfi special audio, mirrored
and turned 2 degrees, a Snapchat caption bar, recorded as an iPhone 16 Pro
Max in the USA. The knobs live in `snapchat_iphone16_usa.json` next to this
file; edit the JSON, not the code.

    from ofmhelpers.repurpose.pipelines.snapchat_iphone16_usa import run
    run("in.mp4", "out.mp4", caption="Going a little too far?", seed=7)

    python -m ofmhelpers.repurpose.pipelines.snapchat_iphone16_usa \
        in.mp4 out.mp4 --caption "Going a little too far?"

Same seed = same copy (every knob draws from it).
"""

import argparse
from datetime import datetime
from pathlib import Path

from ofmhelpers.repurpose.repurpose import Repurpose, RunResult

DEFAULT_CAPTION = "Going a little too far?"
_PRESET = Path(__file__).with_suffix(".json")


def run(
    src: str | Path,
    dst: str | Path,
    *,
    caption: str = DEFAULT_CAPTION,
    seed: int | None = None,
    recorded_at: datetime | None = None,
    timeout_s: int = 600,
) -> RunResult:
    """The JSON preset, then the pipeline's own fixed look: mirrored, turned
    2 degrees, the caption bar; recorded on the iphone16_usa device."""
    job = Repurpose(
        src, dst, seed=seed, recorded_at=recorded_at, timeout_s=timeout_s
    ).apply_json_preset(str(_PRESET))
    job.video = job.video.model_copy(
        update={
            "mirror": 1.0,  # every copy, not a chance
            "rotate": 2.0,  # degrees
            "snapchat_caption": caption,
        }
    )
    result = job.run()
    _verify_audio(result.output)
    return result


_MEAN_MIN_DB = -35.0  # quieter = effectively silent
_MEAN_MAX_DB = -6.0  # hotter = a crushed wall
# A real recording has dynamics: mean sits this far under the peak.
_MIN_DYNAMICS_DB = 4.0


def _verify_audio(dst: Path) -> None:
    """The copy must sound like something: ffmpeg 8.0.1's vibrato filter
    intermittently blows a chain up to a full-scale wall (measured 2026-10-30,
    ~30% of runs, any params) -- and a wall of constant amplitude IS silence
    to a speaker. Raise before a bad copy ships."""
    from ofmhelpers.repurpose.runner import run_log
    from ofmhelpers.utils.ffmpeg import run

    out = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(dst),
        ],
        60,
    )
    if not out.strip():
        msg = f"{dst.name}: no audio stream"
        raise RuntimeError(msg)
    import re

    log = run_log(
        [
            "ffmpeg",
            "-nostats",
            "-loglevel",
            "info",
            "-i",
            str(dst),
            "-map",
            "0:a:0",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        300,
    )
    mean_m = re.search(r"mean_volume: (-?[\d.]+)", log)
    max_m = re.search(r"max_volume: (-?[\d.]+)", log)
    if mean_m is None or max_m is None:
        msg = f"{dst.name}: volumedetect printed no levels"
        raise RuntimeError(msg)
    mean, max_v = float(mean_m.group(1)), float(max_m.group(1))
    if not (_MEAN_MIN_DB <= mean <= _MEAN_MAX_DB):
        msg = f"{dst.name}: audio mean {mean:.1f} dB outside {_MEAN_MIN_DB}..{_MEAN_MAX_DB}"
        raise RuntimeError(msg)
    if max_v - mean < _MIN_DYNAMICS_DB:
        msg = f"{dst.name}: audio has no dynamics (max {max_v:.1f}, mean {mean:.1f})"
        raise RuntimeError(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src", type=Path)
    parser.add_argument("dst", type=Path)
    parser.add_argument("--caption", default=DEFAULT_CAPTION)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    result = run(args.src, args.dst, caption=args.caption, seed=args.seed)
    print(f"{result.output} ({result.device}, {result.seconds}s)")


if __name__ == "__main__":
    main()
