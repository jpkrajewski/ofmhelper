"""ffmpeg for a long clone: measure, cut one piece, join pieces.

The output keeps the source's shape exactly: `join` fits every generated
piece into a frame of the source's aspect ratio at the pieces' own height
(scaled, never cropped; bars only when Wan's nearest ratio differs, e.g. a
4:5 source made at 3:4). Cut and join re-encode (H.264, faststart): a
stream copy cuts only at keyframes. The join reads the pieces through the
concat demuxer, one at a time: the concat filter opens every piece at once
(40 x 15 s at 720p peaked at 2.5 GB, 2026-10-23). `-xerror`: a piece that
cannot be read fails the join instead of ending the video early (ffmpeg
exits 0 on it otherwise). Every output is written to a temp name and
renamed, so a killed ffmpeg never leaves a short file a re-run would take
as done.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ofmhelpers.utils.ffmpeg import binary, run
from ofmhelpers.utils.video_probe import duration_s
from ofmhelpers.web.clone_long.models import Shape

if TYPE_CHECKING:
    from pathlib import Path

    from ofmhelpers.web.clone_long.models import Piece

# A display rotation that turns the picture on its side (90 or 270).
_SIDEWAYS = 90

_VIDEO = ("-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p")


class VideoTools:
    """ffmpeg with one time limit per call."""

    def __init__(self, timeout_s: int) -> None:
        self.timeout_s = timeout_s

    def length(self, src: Path) -> float:
        return duration_s(src, self.timeout_s)

    def shape(self, src: Path) -> Shape:
        """The picture as it plays: a phone video stored sideways (rotation
        90/270) is its turned shape."""
        out = run(
            [
                binary("ffprobe"),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:stream_side_data=rotation",
                "-of",
                "default=noprint_wrappers=1",
                str(src),
            ],
            self.timeout_s,
        )
        found = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        width, height = int(found["width"]), int(found["height"])
        if abs(int(float(found.get("rotation", "0")))) % 180 == _SIDEWAYS:
            width, height = height, width
        return Shape(width=width, height=height)

    def cut(
        self,
        src: Path,
        piece: Piece,
        dest: Path,
        size: tuple[int, int] | None = None,
    ) -> Path:
        """`piece` of `src`, scaled to `size` (width, height) when given."""
        scale = ["-vf", f"scale={size[0]}:{size[1]},setsar=1"] if size else []
        return self._write(
            [
                "-ss",
                f"{piece.start:.3f}",
                "-i",
                str(src),
                "-t",
                f"{piece.length:.3f}",
                *scale,
                *_VIDEO,
                "-c:a",
                "aac",
            ],
            dest,
        )

    def join(self, parts: list[Path], dest: Path, shape: Shape) -> Path:
        """`parts` one after another, as one video of `shape`'s aspect ratio
        at the first part's height. No sound (a long clone has none)."""
        width, height = shape.size_at(self.shape(parts[0]).height)
        fit = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
        listing = dest.with_suffix(".txt")
        listing.parent.mkdir(parents=True, exist_ok=True)
        listing.write_text("".join(f"file '{_quoted(p)}'\n" for p in parts))
        try:
            return self._write(
                [
                    "-xerror",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(listing),
                    "-vf",
                    fit,
                    "-an",
                    *_VIDEO,
                ],
                dest,
            )
        finally:
            listing.unlink(missing_ok=True)

    def _write(self, args: list[str], dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part.mp4")
        try:
            run(
                [
                    binary("ffmpeg"),
                    "-y",
                    "-loglevel",
                    "error",
                    *args,
                    "-movflags",
                    "+faststart",
                    str(tmp),
                ],
                self.timeout_s,
            )
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        return dest


def _quoted(path: Path) -> str:
    """`path` for a concat listing's `file '...'` line: absolute (a relative
    one is read from the listing's folder), its quotes escaped."""
    return str(path.resolve()).replace("'", "'\\''")
