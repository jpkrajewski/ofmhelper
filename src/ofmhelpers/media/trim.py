"""ffmpeg for /edit: cut one part of a video, and the strip of frames its
timeline shows.

The cut re-encodes (H.264 CRF 18, AAC, faststart): a stream copy cuts only
at keyframes, so a part picked as 15.0 s could come out longer than a
model's 15 s limit. Its short side is capped (`short_side`): an 8K source
held 5.8 GB at full size and 343 MB scaled to 1080 (2026-11-06). `-ss`
before `-i` seeks without decoding what comes before. Only the first video
and audio stream are kept. A phone video stored sideways comes out upright
(ffmpeg applies the rotation before the filters, so `iw`/`ih` are the
upright picture's).

The strip decodes every frame of a short video but only keyframes of a long
one (`keyframes_only`): every frame of an hour of 8K is half an hour of
decoding.

Both write to a temp name of their own and rename, so a killed ffmpeg never
leaves a half file under the real name and two writers never share one.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ofmhelpers.media.ffmpeg import binary, run

if TYPE_CHECKING:
    from pathlib import Path


def _short_side(side: int) -> str:
    """A landscape picture's height, else its width, at most `side` px (never
    scaled up); the other side follows, even."""
    return (
        f"scale=w='if(gt(iw,ih),-2,min({side},iw))':h='if(gt(iw,ih),min({side},ih),-2)'"
    )


def cut(
    src: Path,
    start: float,
    end: float,
    dest: Path,
    *,
    short_side: int,
    threads: int,
    timeout_s: int,
) -> Path:
    """`src` from `start` to `end` seconds into `dest` (an .mp4)."""
    return _write(
        [
            "-threads",
            str(threads),
            "-ss",
            f"{start:.3f}",
            "-i",
            str(src),
            "-t",
            f"{end - start:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            _short_side(short_side),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            str(threads),
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
        ],
        dest,
        timeout_s,
    )


def filmstrip(
    src: Path,
    seconds: float,
    dest: Path,
    *,
    frames: int,
    height: int,
    keyframes_only: bool,
    threads: int,
    timeout_s: int,
) -> Path:
    """`frames` frames spread evenly over `src` (`seconds` long), side by
    side in one JPEG `height` px tall."""
    skip = ["-skip_frame", "nokey"] if keyframes_only else []
    return _write(
        [
            "-threads",
            str(threads),
            *skip,
            "-i",
            str(src),
            "-vf",
            f"fps={frames}/{max(seconds, 0.1):.3f},scale=-2:{height},tile={frames}x1",
            "-frames:v",
            "1",
            "-q:v",
            "4",
        ],
        dest,
        timeout_s,
    )


def _write(args: list[str], dest: Path, timeout_s: int) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".part-{uuid.uuid4().hex}-{dest.name}")
    try:
        run(
            [binary("ffmpeg"), "-y", "-loglevel", "error", *args, str(tmp)],
            timeout_s,
        )
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest
