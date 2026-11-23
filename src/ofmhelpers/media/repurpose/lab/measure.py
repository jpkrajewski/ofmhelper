# Copyright (C) 2026 jpkrajewski

import itertools
import json
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.media.ffmpeg import binary, run
from ofmhelpers.media.repurpose import Repurpose
from ofmhelpers.media.repurpose.checker import check
from ofmhelpers.media.repurpose.command import input_args
from ofmhelpers.media.repurpose.lab.space import Part, preset
from ofmhelpers.media.repurpose.transformations.video import (
    VideoDraws,
    VideoSettings,
    VideoTransformer,
)
lazy from ofmhelpers.media.repurpose.repurpose import EncodeStep, RunResult

logger = get_logger(__name__)

DEVICE = "strip"
_TIMEOUT_S = 900
_FPS = 30
_NO_LOOK = {
    "saturation": None,
    "contrast": None,
    "brightness": None,
    "gamma": None,
    "colortemp": None,
    "tone": None,
    "tint": None,
    "sharpen": None,
    "black_and_white": False,
    "vignette": None,
    "chroma": None,
    "noise": None,
    "exposure": None,
    "look": "none",
    "border": None,
    "pattern": None,
    "pattern_opacity": None,
    "sharpen_late": None,
    "particles": None,
    "adjust": None,
}
_RATE = 16000
_FRAME = 512
_HOP = 256
_BANDS = 48
_MAX_LAG = 40
_MIN_FRAMES = 50
_MIN_LOUD = 25


class Measurement(BaseModel):
    trial: str
    sample: str
    seed: int
    part: Part
    video: dict[str, int] = {}
    audio: int | None = None
    tmk_level2: float | None = None
    vmaf: float | None = None
    audio_change_db: float | None = None
    seconds: float = 0.0
    error: str | None = None

    @property
    def video_caught(self) -> int:
        return max(self.video.values(), default=0)


def reference(
    draws: VideoDraws,
    fps: str | None,
    sample: Path,
    dst: Path,
    work: Path,
) -> None:
    ref = VideoTransformer.from_draws(
        VideoSettings(),
        draws.model_copy(update=_NO_LOOK),
    )
    run(
        [
            binary("ffmpeg"),
            "-y",
            "-loglevel",
            "error",
            *input_args(sample, ref),
            *(["-r", fps] if fps else []),
            "-vf",
            ref.filters(work),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "6",
            str(dst),
        ],
        _TIMEOUT_S,
    )


def vmaf(copy: Path, ref: Path, work: Path) -> float:
    graph = (
        f"[0:v]setpts=PTS-STARTPTS,fps={_FPS},format=yuv420p[d];"
        f"[1:v]setpts=PTS-STARTPTS,fps={_FPS},format=yuv420p[r];"
    )
    tool = settings.repurpose.lab_vmaf_ffmpeg
    if tool:
        log = work / "vmaf.json"
        graph += f"[d][r]libvmaf=log_fmt=json:log_path={log}:n_threads=2:n_subsample=2:shortest=1"  # noqa: E501
        subprocess.run(
            [
                tool,
                "-v",
                "error",
                "-i",
                str(copy),
                "-i",
                str(ref),
                "-lavfi",
                graph,
                "-f",
                "null",
                "-",
            ],
            check=True,
            capture_output=True,
            timeout=_TIMEOUT_S,
        )
        return round(json.loads(log.read_text())["pooled_metrics"]["vmaf"]["mean"], 2)
    graph += "[d][r]ssim=shortest=1"
    out = subprocess.run(
        [
            binary("ffmpeg"),
            "-i",
            str(copy),
            "-i",
            str(ref),
            "-lavfi",
            graph,
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
    ).stderr
    return round(float(out.rsplit("All:", 1)[1].split()[0]) * 100, 2)


def _pcm(path: Path, start: float | None, seconds: float | None) -> np.ndarray:
    argv = [binary("ffmpeg"), "-v", "error"]
    if start:
        argv += ["-ss", f"{start:.3f}"]
    if seconds:
        argv += ["-t", f"{seconds:.3f}"]
    argv += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(_RATE), "-f", "f32le", "-"]
    raw = subprocess.run(
        argv,
        check=True,
        capture_output=True,
        timeout=_TIMEOUT_S,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _bands(pcm: np.ndarray) -> np.ndarray:
    frames = len(pcm) // _HOP - _FRAME // _HOP
    if frames <= 0:
        return np.zeros((0, _BANDS))
    idx = np.arange(_FRAME)[None, :] + _HOP * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(pcm[idx] * np.hanning(_FRAME), axis=1)) ** 2
    freqs = np.fft.rfftfreq(_FRAME, 1 / _RATE)
    edges = np.geomspace(80, 7600, _BANDS + 1)
    out = np.stack(
        [
            spec[:, (freqs >= lo) & (freqs < hi)].sum(axis=1)
            for lo, hi in itertools.pairwise(edges)
        ],
        axis=1,
    )
    return 10 * np.log10(out + 1e-10)


def audio_change_db(copy: Path, sample: Path, start: float, seconds: float) -> float:
    a = _bands(_pcm(sample, start, seconds))
    b = _bands(_pcm(copy, None, None))
    loud = a.max() - 60
    best = float("inf")
    for lag in range(-_MAX_LAG, _MAX_LAG + 1):
        x, y = (a[lag:], b) if lag >= 0 else (a, b[-lag:])
        n = min(len(x), len(y))
        if n < _MIN_FRAMES:
            continue
        mask = x[:n].max(axis=1) > loud
        if mask.sum() < _MIN_LOUD:
            continue
        best = min(best, float(np.abs(x[:n][mask] - y[:n][mask]).mean()))
    return round(best, 2)


def _require_step(result: RunResult) -> EncodeStep:
    if result.step is None:
        msg = f"device {DEVICE} made no encode step"
        raise RuntimeError(msg)
    return result.step


def measure(
    trial: str,
    strengths: dict[str, float],
    part: Part,
    sample: Path,
    seed: int,
    *,
    full: bool = False,
    keep: Path | None = None,
) -> Measurement:
    started = time.monotonic()
    out = Measurement(trial=trial, sample=sample.stem, seed=seed, part=part)
    try:
        with tempfile.TemporaryDirectory(prefix="lab_") as tmp:
            work = Path(tmp)
            copy = work / "copy.mp4"
            knobs = preset(strengths)
            maker = Repurpose(sample, copy, seed=seed, timeout_s=_TIMEOUT_S)
            maker.apply_device_preset(DEVICE)
            maker.video, maker.audio = knobs.video, knobs.audio
            result = maker.run()
            _require_step(result)
            report = check(sample, copy, timeout_s=_TIMEOUT_S)
            out.video = {
                h.kind.value: round(h.sequence.share * 100) for h in report.hashes
            } | {d.kind: round(d.sequence.share * 100) for d in report.descriptors}
            if report.tmk:
                weaker = min(report.tmk.level1, report.tmk.level2)
                out.video["tmk"] = min(100, round(weaker / report.tmk.threshold * 50))
                out.tmk_level2 = report.tmk.level2
            out.audio = (
                round(report.audio.matched_share * 100) if report.audio else None
            )
            step = _require_step(result)
            if full or part is Part.VIDEO:
                ref = work / "ref.mp4"
                reference(step.video, step.encode.fps, sample, ref, work)
                out.vmaf = vmaf(copy, ref, work)
            if (full or part is Part.AUDIO) and report.audio:
                t = step.video.timeline
                out.audio_change_db = audio_change_db(
                    copy,
                    sample,
                    t.start_s,
                    t.duration_s,
                )
            if keep:
                keep.parent.mkdir(parents=True, exist_ok=True)
                keep.write_bytes(copy.read_bytes())
    except Exception as exc:
        logger.warning("lab: %s on %s not measured", trial, sample.name, exc_info=True)
        out.error = f"{type(exc).__name__}: {exc}"[:500]
    out.seconds = round(time.monotonic() - started, 1)
    return out
