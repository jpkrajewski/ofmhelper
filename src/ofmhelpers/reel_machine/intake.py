"""
Reel intake: get the reel onto disk and measure how long it is. That is the
whole job now -- the frame extraction, whisper transcript, and diarization
this module used to do were all inputs to a local prompt builder that no
longer exists; the LLM watches the video itself.

The one leftover ffmpeg call is `build_contact_sheet`, used ONLY by the
Anthropic provider (the Claude Messages API takes images, not video -- see
llm/anthropic_provider.py). Gemini never calls it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ofmhelpers.downloaders.generic import DownloadConfig, download
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


@dataclass
class IntakeResult:
    video_path: Path
    duration: float
    source_url: str | None = None


def fetch_source(url_or_path: str, out_dir: Path) -> Path:
    """A local file path is used directly (copied into the job's working
    directory); anything else is treated as a reel URL and downloaded via
    yt-dlp. A blocked/failed download raises -- the web layer surfaces that
    as the job's error message rather than silently producing nothing."""
    out_dir.mkdir(parents=True, exist_ok=True)

    candidate = Path(url_or_path)
    if candidate.is_file():
        dest = out_dir / f"reference{candidate.suffix or '.mp4'}"
        dest.write_bytes(candidate.read_bytes())
        return dest

    result = download(url_or_path, DownloadConfig(output_dir=out_dir))
    if not result.success or not result.output_paths:
        hint = ""
        if "instagram.com" in url_or_path.lower():
            # Instagram rejects most reel-info requests outright when
            # logged out (this is what a yt-dlp "HTTP Error 400" /
            # "Video info extraction failed" on an Instagram URL almost
            # always means) -- this repo already has cookie support for
            # exactly this (downloaders/cookies.py -> get_cookiefile(),
            # picked up automatically by every yt-dlp call), it's just not
            # configured yet.
            hint = (
                " Instagram blocks logged-out downloads for most reels -- export "
                "your Instagram cookies (e.g. the 'Get cookies.txt LOCALLY' browser "
                "extension, while logged into instagram.com) and upload the file at "
                "/cookies, then try again. If the download still fails after that, "
                "save the reel via any reel-downloader site and upload the .mp4 "
                "directly on the /replicate form instead of pasting the link."
            )
        msg = f"Could not download {url_or_path}: {result.error}.{hint}"
        raise RuntimeError(msg)
    return result.output_paths[0]


def _run_ffmpeg(cmd: list[str]) -> None:
    """Runs an ffmpeg command, surfacing real stderr in the raised error
    instead of a bare CalledProcessError with no message -- matches the
    pattern web/routers/generation/fake_ai.py already uses for its own ffmpeg
    call."""
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as exc:
        msg = "ffmpeg isn't installed/on PATH"
        raise RuntimeError(msg) from exc
    except subprocess.CalledProcessError as exc:
        msg = f"ffmpeg failed: {exc.stderr.decode(errors='replace')[-800:]}"
        raise RuntimeError(msg) from exc


def build_contact_sheet(video_path: Path, out_dir: Path, fps: int = 1) -> Path:
    """One 4x4 tile of 1-fps frames. Only the Anthropic provider needs this
    (Claude's vision is image-only); Gemini gets the real video file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    contact_sheet = out_dir / "contact-sheet.jpg"
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps={fps},scale=320:-1,tile=4x4",
            # -update 1: we're writing ONE static image, not an image
            # sequence -- some ffmpeg builds hard-error on the image2 muxer
            # without this ("does not contain an image sequence pattern"),
            # others only warn; -update 1 makes it unambiguous everywhere.
            "-frames:v",
            "1",
            "-update",
            "1",
            str(contact_sheet),
        ]
    )
    return contact_sheet


def probe_duration(video_path: Path) -> float:
    """Source reel duration in seconds via ffprobe -- the generated clone
    must always match this exactly (see reel_machine/CLAUDE.md), so there's
    no manual "Length" field for the user to get wrong."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            check=True,
            text=True,
        )
    except FileNotFoundError as exc:
        msg = "ffprobe isn't installed/on PATH"
        raise RuntimeError(msg) from exc
    except subprocess.CalledProcessError as exc:
        msg = f"ffprobe failed: {exc.stderr.strip()}"
        raise RuntimeError(msg) from exc
    return float(result.stdout.strip())


def run_intake(url_or_path: str, work_dir: Path) -> IntakeResult:
    video_path = fetch_source(url_or_path, work_dir)
    return IntakeResult(
        video_path=video_path,
        duration=probe_duration(video_path),
        source_url=url_or_path if not Path(url_or_path).is_file() else None,
    )
