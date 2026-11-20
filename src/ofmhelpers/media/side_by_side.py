lazy from pathlib import Path

from ofmhelpers.media import ffmpeg

_FPS = 30


def _half(index: int, height: int, label: str) -> str:
    return f"[{index}:v]fps={_FPS},scale=-2:{height},setsar=1,format=yuv420p[{label}]"


def side_by_side(
    left: Path, right: Path, dest: Path, *, height: int, timeout_s: int
) -> None:
    joined = ";".join(
        [
            _half(0, height, "l"),
            _half(1, height, "r"),
            "[l][r]hstack=inputs=2:shortest=1[v]",
        ]
    )
    ffmpeg.run(
        [
            ffmpeg.binary("ffmpeg"),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(left),
            "-i",
            str(right),
            "-filter_complex",
            joined,
            "-map",
            "[v]",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "2",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-shortest",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(dest),
        ],
        timeout_s,
    )
