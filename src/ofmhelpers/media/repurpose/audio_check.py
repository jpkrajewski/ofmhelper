import re
lazy from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.media.ffmpeg import run, run_log

_MAX_DROP_DB = 10.0
_MEAN_MAX_DB = -6.0
_MIN_DYNAMICS_DB = 4.0
_DYNAMICS_LOSS_DB = 3.0
_MEAN = re.compile(r"mean_volume: (-?[\d.]+)")
_MAX = re.compile(r"max_volume: (-?[\d.]+)")


class BrokenAudioError(RuntimeError): ...


class AudioLevels(BaseModel, frozen=True):
    mean_db: float
    max_db: float


def levels(path: Path) -> AudioLevels | None:
    probe = ["ffprobe", "-v", "error", "-select_streams", "a:0"]
    probe += ["-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)]
    if not run(probe, 60).strip():
        return None
    detect = ["ffmpeg", "-nostats", "-loglevel", "info", "-i", str(path)]
    detect += ["-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"]
    log = run_log(detect, 300)
    mean, peak = _MEAN.search(log), _MAX.search(log)
    if mean is None or peak is None:
        msg = f"{path.name}: volumedetect printed no levels"
        raise RuntimeError(msg)
    return AudioLevels(mean_db=float(mean.group(1)), max_db=float(peak.group(1)))


def verify_audio(src: Path, dst: Path) -> None:
    copy = levels(dst)
    if copy is None:
        msg = f"{dst.name}: no audio stream"
        raise BrokenAudioError(msg)
    source = levels(src)
    if source is not None and copy.mean_db < source.mean_db - _MAX_DROP_DB:
        msg = f"{dst.name}: audio mean {copy.mean_db:.1f} dB, source {source.mean_db:.1f} dB"
        raise BrokenAudioError(msg)
    if copy.mean_db > _MEAN_MAX_DB:
        msg = f"{dst.name}: audio mean {copy.mean_db:.1f} dB over {_MEAN_MAX_DB}"
        raise BrokenAudioError(msg)
    floor = _MIN_DYNAMICS_DB
    if source is not None:
        floor = min(floor, source.max_db - source.mean_db - _DYNAMICS_LOSS_DB)
    if copy.max_db - copy.mean_db < floor:
        msg = f"{dst.name}: audio has no dynamics (max {copy.max_db:.1f}, mean {copy.mean_db:.1f})"
        raise BrokenAudioError(msg)
