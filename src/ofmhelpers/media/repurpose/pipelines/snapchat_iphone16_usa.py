import argparse
from datetime import datetime
from pathlib import Path

from ofmhelpers.media.repurpose.repurpose import Repurpose, RunResult

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
    job = Repurpose(
        src, dst, seed=seed, recorded_at=recorded_at, timeout_s=timeout_s
    ).apply_json_preset(str(_PRESET))
    job.video = job.video.model_copy(
        update={
            "mirror": 1.0,
            "rotate": 1.0,
            "snapchat_caption": caption,
        }
    )
    result = job.run()
    _verify_audio(Path(src), result.output)
    return result


_MAX_DROP_DB = 10.0
_MEAN_MAX_DB = -6.0
_MIN_DYNAMICS_DB = 4.0
_DYNAMICS_LOSS_DB = 3.0


def _levels(path: Path) -> tuple[float, float] | None:
    import re

    from ofmhelpers.media.ffmpeg import run, run_log

    probe = ["ffprobe", "-v", "error", "-select_streams", "a:0"]
    probe += ["-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)]
    if not run(probe, 60).strip():
        return None
    detect = ["ffmpeg", "-nostats", "-loglevel", "info", "-i", str(path)]
    detect += ["-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"]
    log = run_log(detect, 300)
    mean_m = re.search(r"mean_volume: (-?[\d.]+)", log)
    max_m = re.search(r"max_volume: (-?[\d.]+)", log)
    if mean_m is None or max_m is None:
        msg = f"{path.name}: volumedetect printed no levels"
        raise RuntimeError(msg)
    return float(mean_m.group(1)), float(max_m.group(1))


def _verify_audio(src: Path, dst: Path) -> None:
    copy = _levels(dst)
    if copy is None:
        msg = f"{dst.name}: no audio stream"
        raise RuntimeError(msg)
    source = _levels(src)
    mean, max_v = copy
    if source is not None and mean < source[0] - _MAX_DROP_DB:
        msg = f"{dst.name}: audio mean {mean:.1f} dB, source {source[0]:.1f} dB"
        raise RuntimeError(msg)
    if mean > _MEAN_MAX_DB:
        msg = f"{dst.name}: audio mean {mean:.1f} dB over {_MEAN_MAX_DB}"
        raise RuntimeError(msg)
    floor = _MIN_DYNAMICS_DB
    if source is not None:
        floor = min(floor, source[1] - source[0] - _DYNAMICS_LOSS_DB)
    if max_v - mean < floor:
        msg = f"{dst.name}: audio has no dynamics (max {max_v:.1f}, mean {mean:.1f})"
        raise RuntimeError(msg)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("src", type=Path)
    parser.add_argument("dst", type=Path)
    parser.add_argument("--caption", default=DEFAULT_CAPTION)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    result = run(args.src, args.dst, caption=args.caption, seed=args.seed)
    print(f"{result.output} ({result.device}, {result.seconds}s)")


if __name__ == "__main__":
    main()
