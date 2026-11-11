"""ffmpeg for /edit: cut one part of a video, the light copy the page plays,
the strip of frames its timeline shows, and one frame as a picture.

The cut re-encodes (H.264 CRF 18, AAC, faststart): a stream copy cuts only
at keyframes, so a part picked as 15.0 s could come out longer than a
model's 15 s limit. It comes out at the short side asked (`scaled_to`, up
or down; at most 4K: an 8K source held 5.8 GB at full size, 1.35 GB cut to
4K, 2026-11-06/07). `-ss` and
`-t` before `-i` read only the part, without decoding what comes before.
Only the first video and audio stream are kept. A phone video stored
sideways comes out upright (ffmpeg applies the rotation before the filters,
so `iw`/`ih` are the upright picture's). The picture's own filters run
first (`picture`: a turn and mirror, `turn_filter`, then a crop,
`crop_filter`), then the speed (`setpts` + `atempo`, pitch kept), then the
scale. A frame grab takes the same picture filters and scale.

The page's copy (`proxy`) is small (short side 540) with a keyframe every
`gop` frames and no B-frames, so a seek in the browser is one short decode
of a file it already has, and every frame keeps the source's time
(`-fps_mode passthrough`): the frame the page shows at t is the one the cut
starts at and the grab saves. The original streamed from the server: a seek
took 1.75 s and a 5 s drag showed one frame (dev, 250 ms RTT / 8 Mbps, the
India VA's 1440p VP9, 2026-11-07); this copy: 7 ms, 51 frames.

The strip decodes every frame of a short video but only keyframes of a long
one (`keyframes_only`).

Files are written to a temp name of their own and renamed, so a killed
ffmpeg never leaves a half file under the real name and two writers never
share one.
"""

import uuid
lazy from pathlib import Path

from ofmhelpers.media.ffmpeg import binary, run, run_bytes
lazy from ofmhelpers.media.size import Size


def _short_side(side: int) -> str:
    """A landscape picture's height, else its width, at most `side` px (never
    scaled up), even (a 361 px wide source failed in libx264); the other side
    follows, even."""
    w = f"trunc(min({side},iw)/2)*2"
    h = f"trunc(min({side},ih)/2)*2"
    return f"scale=w='if(gt(iw,ih),-2,{w})':h='if(gt(iw,ih),{h},-2)'"


def scaled_to(side: int) -> str:
    """Short side exactly `side` px, up or down (lanczos); the other side
    follows, even."""
    return (
        f"scale=w='if(gt(iw,ih),-2,{side})':h='if(gt(iw,ih),{side},-2)':flags=lanczos"
    )


_TURNS = {90: "transpose=clock", 180: "hflip,vflip", 270: "transpose=cclock"}


def turn_filter(turn: int, mirror: bool) -> str | None:
    """`turn` degrees clockwise (0, 90, 180, 270), then left-right mirrored."""
    return _filters(_TURNS.get(turn), "hflip" if mirror else None) or None


def crop_filter(ratio: Size | None, x: float, y: float, scale: float = 1.0) -> str:
    """A `ratio` (width to height; None: the picture's own) box, `scale` times
    the biggest one inside the upright picture, even-sized, at `x` / `y`
    (0..1) along the room it has to move. Pure ffmpeg expressions: no probe
    of the size or the rotation."""
    if ratio is None:
        w_max, h_max = "iw", "ih"
    else:
        w, h = ratio.width, ratio.height
        w_max, h_max = f"min(iw,ih*{w}/{h})", f"min(ih,iw*{h}/{w})"
    return (
        f"crop=w='trunc({w_max}*{scale:.4f}/2)*2'"
        f":h='trunc({h_max}*{scale:.4f}/2)*2'"
        f":x='(iw-ow)*{x:.4f}':y='(ih-oh)*{y:.4f}'"
    )


def _filters(*parts: str | None) -> str:
    return ",".join(p for p in parts if p)


def cut(
    src: Path,
    start: float,
    end: float,
    dest: Path,
    *,
    short_side: int,
    threads: int,
    timeout_s: int,
    picture: str | None = None,
    speed: float = 1.0,
) -> Path:
    """`src` from `start` to `end` seconds into `dest` (an .mp4), turned and
    cropped (`picture`), played `speed` times as fast, `short_side` px."""
    faster = speed != 1.0
    return _write(
        [
            "-threads",
            str(threads),
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{end - start:.3f}",
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            _filters(
                picture,
                f"setpts=PTS/{speed:g}" if faster else None,
                scaled_to(short_side),
            ),
            *(["-af", f"atempo={speed:g}"] if faster else []),
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


def proxy(
    src: Path,
    dest: Path,
    *,
    short_side: int,
    gop: int,
    crf: int,
    threads: int,
    timeout_s: int,
) -> Path:
    """The page's light copy of `src` (an .mp4): every frame at its own
    time, a keyframe every `gop` frames, no B-frames."""
    return _write(
        [
            "-threads",
            str(threads),
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            _short_side(short_side),
            "-fps_mode",
            "passthrough",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(crf),
            "-g",
            str(gop),
            "-keyint_min",
            str(gop),
            "-bf",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            str(threads),
            "-c:a",
            "aac",
            "-b:a",
            "64k",
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


def frame(
    src: Path,
    at: float,
    *,
    short_side: int,
    threads: int,
    timeout_s: int,
    picture: str | None = None,
) -> bytes:
    """The first frame at or after `at` seconds of `src`, turned and cropped
    (`picture`), `short_side` px, as a JPEG."""
    return run_bytes(
        [
            binary("ffmpeg"),
            "-loglevel",
            "error",
            "-threads",
            str(threads),
            "-ss",
            f"{at:.4f}",
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-vf",
            _filters(picture, scaled_to(short_side)),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-f",
            "image2pipe",
            "-c:v",
            "mjpeg",
            "-",
        ],
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
