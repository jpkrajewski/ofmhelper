# Copyright (C) 2026 jpkrajewski

lazy from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.features.clone_long.models import Shape
from ofmhelpers.media.ffmpeg import binary, run, run_bytes
from ofmhelpers.media.video_probe import duration_s
lazy from ofmhelpers.features.clone_long.models import Piece

_SIDEWAYS = 90

_VIDEO = ("-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p")

_GAIN = (0.75, 1.33)


class VideoTools:
    def __init__(self, timeout_s: int) -> None:
        self.timeout_s = timeout_s

    def length(self, src: Path) -> float:
        return duration_s(src, self.timeout_s)

    def shape(self, src: Path) -> Shape:
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
        size: Shape | None = None,
    ) -> Path:
        scale = ["-vf", f"scale={size.width}:{size.height},setsar=1"] if size else []
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

    def last_frame(self, src: Path, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        run(
            [
                binary("ffmpeg"),
                "-y",
                "-loglevel",
                "error",
                "-sseof",
                "-0.2",
                "-i",
                str(src),
                "-update",
                "1",
                str(dst),
            ],
            self.timeout_s,
        )
        return dst

    def _mean_rgb(self, src: Path) -> _Rgb:
        raw = run_bytes(
            [
                binary("ffmpeg"),
                "-v",
                "error",
                "-i",
                str(src),
                "-vf",
                "scale=1:1,format=rgb24",
                "-f",
                "rawvideo",
                "-",
            ],
            self.timeout_s,
        )
        n = max(1, len(raw) // 3)
        return _Rgb(
            red=sum(raw[0::3]) / n,
            green=sum(raw[1::3]) / n,
            blue=sum(raw[2::3]) / n,
        )

    def _colour_matched(
        self,
        parts: list[Path],
        dests: list[Path],
        *,
        audio: bool,
    ) -> list[Path]:
        ref = self._mean_rgb(parts[0])
        keep = ["-c:a", "copy"] if audio else ["-an"]
        for part, dst in zip(parts[1:], dests, strict=True):
            m = self._mean_rgb(part)
            gains = ref.gains_to(m)
            self._write(
                [
                    "-i",
                    str(part),
                    "-vf",
                    f"colorchannelmixer={gains},format=yuv420p",
                    *keep,
                    *_VIDEO,
                ],
                dst,
            )
        return dests

    def join(  # noqa: PLR0913
        self,
        parts: list[Path],
        dest: Path,
        shape: Shape,
        *,
        work: Path,
        colour_match: bool = False,
        audio: bool = False,
    ) -> Path:
        size = shape.size_at(self.shape(parts[0]).height)
        width, height = size.width, size.height
        fit = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
        sound = ["-c:a", "aac", "-b:a", "192k"] if audio else ["-an"]
        listing = work / "join.txt"
        matched = (
            [work / f"cm_{i:03d}.mp4" for i in range(1, len(parts))]
            if colour_match
            else []
        )
        try:
            if matched:
                parts = [parts[0], *self._colour_matched(parts, matched, audio=audio)]
            work.mkdir(parents=True, exist_ok=True)
            listing.write_text("".join(f"file '{_quoted(p)}'\n" for p in parts))
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
                    *sound,
                    *_VIDEO,
                ],
                dest,
            )
        finally:
            for made in (listing, *matched):
                made.unlink(missing_ok=True)

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


class _Rgb(BaseModel, frozen=True):
    red: float
    green: float
    blue: float

    def gains_to(self, other: _Rgb) -> str:
        pairs = (
            ("r", self.red, other.red),
            ("gg", self.green, other.green),
            ("b", self.blue, other.blue),
        )
        return ":".join(f"{c}={_gain(ref, m):.4f}" for c, ref, m in pairs)


def _gain(ref: float, mean: float) -> float:
    return min(max(ref / mean, _GAIN[0]), _GAIN[1]) if mean else 1.0


def _quoted(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")
