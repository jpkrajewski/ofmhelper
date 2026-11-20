import argparse
from pathlib import Path
lazy from datetime import datetime

from ofmhelpers.media.repurpose.audio_check import verify_audio
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
    verify_audio(Path(src), result.output)
    return result


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
