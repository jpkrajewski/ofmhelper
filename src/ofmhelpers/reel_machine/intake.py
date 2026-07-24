"""
Reel intake: fetch a reel -> extract frames -> word-level transcript ->
(optional) speaker diarization.

Ports the old reel-machine skill bundle's Bash pipeline (yt-dlp -> ffmpeg ->
whisper -> pyannote) into plain Python, reusing existing house code wherever
it already exists instead of re-implementing it (the download step is just
ofmhelpers.downloaders.generic.download, the same downloader every other
reel/video tool in this repo uses).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.downloaders.generic import DownloadConfig, download


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Transcript:
    text: str
    words: list[Word] = field(default_factory=list)
    language: str | None = None


@dataclass
class IntakeResult:
    video_path: Path
    frames_dir: Path
    contact_sheet: Path
    transcript: Transcript
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
        raise RuntimeError(f"Could not download {url_or_path}: {result.error}.{hint}")
    return result.output_paths[0]


def _run_ffmpeg(cmd: list[str]) -> None:
    """Runs an ffmpeg command, surfacing real stderr in the raised error
    instead of a bare CalledProcessError with no message -- matches the
    pattern web/routers/fake_ai.py already uses for its own ffmpeg call."""
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg isn't installed/on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"ffmpeg failed: {exc.stderr.decode(errors='replace')[-800:]}"
        ) from exc


def extract_frames(video_path: Path, out_dir: Path, fps: int = 1) -> tuple[Path, Path]:
    """1-fps frames + a 4x4 contact sheet, via ffmpeg (subprocess -- same
    approach fake_ai.py and download_reels.py already use elsewhere in this
    repo, no new dependency)."""
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps={fps}",
            str(frames_dir / "frame-%03d.png"),
        ]
    )

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
            # sequence (frame-%03d.png above is the sequence) -- some
            # ffmpeg builds hard-error on the image2 muxer without this
            # ("does not contain an image sequence pattern"), others only
            # warn; -update 1 makes it unambiguous on every build.
            "-frames:v",
            "1",
            "-update",
            "1",
            str(contact_sheet),
        ]
    )
    return frames_dir, contact_sheet


def transcribe(video_path: Path, model_size: str = "small") -> Transcript:
    """Word-level transcript via faster-whisper -- a free, open-source,
    drop-in replacement for the old reel-machine bundle's `whisper` CLI call
    (same models, several times faster, runs locally, no API key)."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, compute_type="int8")
    segments, info = model.transcribe(str(video_path), word_timestamps=True)

    words: list[Word] = []
    text_parts: list[str] = []
    for segment in segments:
        text_parts.append(segment.text.strip())
        for w in segment.words or []:
            words.append(Word(text=w.word.strip(), start=w.start, end=w.end))

    return Transcript(text=" ".join(text_parts), words=words, language=info.language)


def diarize(video_path: Path, transcript: Transcript) -> Transcript:
    """Optional speaker-diarization pass (pyannote.audio) -- tags each word
    with a speaker label so multi-voice reels can be attributed correctly.

    Not a hard dependency: skips gracefully (returns the transcript
    unchanged) if pyannote/torch aren't installed or no HF token is set,
    exactly like the original bash skill treated this step as optional.
    Install with: pip install 'ofmhelpers[diarization]'
    """
    hf_token = settings.reel_machine.hf_token or settings.reel_machine.huggingface_token
    if not hf_token:
        print("[reel_machine] no HF_TOKEN set, skipping diarization", flush=True)
        return transcript

    try:
        from pyannote.audio import Pipeline
    except ImportError:
        print(
            "[reel_machine] pyannote.audio not installed, skipping diarization "
            "(pip install 'ofmhelpers[diarization]' to enable)",
            flush=True,
        )
        return transcript

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-community-1", token=hf_token
    )
    diarization = pipeline(str(video_path))

    for turn, _, speaker in diarization.itertracks(yield_label=True):
        for word in transcript.words:
            if turn.start <= word.start < turn.end:
                word.text = f"[{speaker}] {word.text}"

    return transcript


def run_intake(url_or_path: str, work_dir: Path) -> IntakeResult:
    """Full Stage 1 pipeline: fetch -> frames -> transcript. Diarization is
    deliberately not run automatically here (heavy optional dependency) --
    call diarize() separately if a reel needs multi-voice attribution."""
    video_path = fetch_source(url_or_path, work_dir)
    frames_dir, contact_sheet = extract_frames(video_path, work_dir)
    transcript = transcribe(video_path)
    return IntakeResult(
        video_path=video_path,
        frames_dir=frames_dir,
        contact_sheet=contact_sheet,
        transcript=transcript,
        source_url=url_or_path if not Path(url_or_path).is_file() else None,
    )
