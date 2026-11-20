import uuid
lazy from pathlib import Path

from ofmhelpers.media.ffmpeg import binary, run, run_bytes
lazy from ofmhelpers.media.size import Size


def _short_side(side: int) -> str:
    w = f"trunc(min({side},iw)/2)*2"
    h = f"trunc(min({side},ih)/2)*2"
    return f"scale=w='if(gt(iw,ih),-2,{w})':h='if(gt(iw,ih),{h},-2)'"


def scaled_to(side: int) -> str:
    return (
        f"scale=w='if(gt(iw,ih),-2,{side})':h='if(gt(iw,ih),{side},-2)':flags=lanczos"
    )


_TURNS = {90: "transpose=clock", 180: "hflip,vflip", 270: "transpose=cclock"}


def turn_filter(turn: int, mirror: bool) -> str | None:
    return _filters(_TURNS.get(turn), "hflip" if mirror else None) or None


def crop_filter(ratio: Size | None, x: float, y: float, scale: float = 1.0) -> str:
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


_JOIN_AUDIO = ("-c:a", "aac", "-ar", "48000", "-ac", "2")


def part(
    src: Path,
    start: float,
    end: float,
    dest: Path,
    *,
    short_side: int,
    frame: Size | None,
    fps: float,
    sound: bool,
    threads: int,
    timeout_s: int,
    picture: str | None = None,
    speed: float = 1.0,
) -> Path:
    faster = speed != 1.0
    fit = (
        f"scale={frame.width}:{frame.height}:force_original_aspect_ratio=decrease"
        f":flags=lanczos,pad={frame.width}:{frame.height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        if frame is not None
        else scaled_to(short_side)
    )
    silence = [
        "-f",
        "lavfi",
        "-t",
        f"{(end - start) / speed:.3f}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
    ]
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
            *([] if sound else silence),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0" if sound else "1:a:0",
            "-vf",
            _filters(
                picture,
                f"setpts=PTS/{speed:g}" if faster else None,
                fit,
                f"fps={fps:g}",
            ),
            *(["-af", f"atempo={speed:g}"] if faster and sound else []),
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
            *_JOIN_AUDIO,
            "-shortest",
            "-movflags",
            "+faststart",
        ],
        dest,
        timeout_s,
    )


def _quoted(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def join(parts: list[Path], dest: Path, *, threads: int, timeout_s: int) -> Path:
    listing = dest.with_name(f".join-{uuid.uuid4().hex}.txt")
    listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text("".join(f"file '{_quoted(p)}'\n" for p in parts))
    try:
        return _write(
            [
                "-xerror",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c:v",
                "copy",
                "-threads",
                str(threads),
                *_JOIN_AUDIO,
                "-movflags",
                "+faststart",
            ],
            dest,
            timeout_s,
        )
    finally:
        listing.unlink(missing_ok=True)


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
