"""Two videos as one, next to each other and in step: the left one, the right
one, both at the same height (/clone's SOP: the video that was cloned next
to its clone, so the difference is seen at a glance).

One H.264 8-bit file every browser plays, whatever the two were (a phone
recording export is HEVC 10-bit HDR), with the right video's sound, as long
as the shorter of the two."""

from pathlib import Path

from ofmhelpers.media import ffmpeg

# Both halves are brought to one frame rate: `hstack` takes its timing from
# the first input and would drop or repeat the other's frames.
_FPS = 30


def _half(index: int, height: int, label: str) -> str:
    return f"[{index}:v]fps={_FPS},scale=-2:{height},setsar=1,format=yuv420p[{label}]"


def side_by_side(
    left: Path, right: Path, dest: Path, *, height: int, timeout_s: int
) -> None:
    """Writes `left` and `right` next to each other into `dest` (mp4)."""
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
