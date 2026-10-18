"""A provider that calls nothing: for testing the generation pages for free.

Pick the outcome (success or error), a delay, and image or video (the two
catalog models); the result is a placeholder frame (a few seconds of it, via
ffmpeg, for video). It goes through the same router, runner and result
handling as a real provider, so the whole pipeline is exercised without
spending credits. References are "uploaded" by path only -- nothing reads
them.
"""

import shutil
import subprocess
import time
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

from ofmhelpers.aigenproviders.base import Provider, TaskState, register_provider
from ofmhelpers.aigenproviders.errors import (
    ProviderGenerationError,
    ProviderInputError,
)
from ofmhelpers.aigenproviders.spec import ModelSpec, Payload
from ofmhelpers.config import settings

_SCHEME = "fake://"


def _workdir() -> Path:
    path = Path(settings.aigen.out_dir) / "fake"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _frame(prompt: str) -> Image.Image:
    img = Image.new("RGB", (768, 768), color=(35, 25, 65))
    draw = ImageDraw.Draw(img)
    draw.text((24, 24), "FAKE AI MODEL", fill=(255, 255, 255))
    draw.text(
        (24, 60), "(this is a placeholder, not a real generation)", fill=(190, 190, 220)
    )
    draw.multiline_text((24, 110), prompt[:500], fill=(255, 255, 255))
    return img


def _video(frame: Path, out: Path) -> None:
    seconds = settings.aigen.fake_video_duration_s
    command = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(frame), "-t", str(seconds),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ]  # fmt: skip
    try:
        subprocess.run(command, capture_output=True, check=True)
    except FileNotFoundError as exc:
        msg = "Fake AI: ffmpeg isn't installed/on PATH, can't fake a video"
        raise ProviderInputError(msg) from exc
    except subprocess.CalledProcessError as exc:
        tail = exc.stderr.decode(errors="replace")[-500:]
        msg = f"Fake AI: ffmpeg failed building the fake video: {tail}"
        raise ProviderGenerationError(msg) from exc


@register_provider
class FakeProvider(Provider):
    name = "fake"
    label = "Fake AI (testing)"
    rank = 1000
    needs_key = False

    @classmethod
    def default_key(cls, admin: bool) -> str | None:
        del admin
        return None

    def upload(self, path: Path) -> str:
        return f"{_SCHEME}{path}"

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        """Generates right here (after the chosen delay): there is no queue
        to wait on. `error` fails the generation the way a provider would."""
        del idempotency_key
        time.sleep(float(payload.get("delay") or 0))  # type: ignore[arg-type]
        if payload.get("outcome") == "error":
            raise ProviderGenerationError(
                str(payload.get("error_message") or "Fake AI: simulated failure")
            )
        task_id = uuid.uuid4().hex[:12]
        frame = _frame(str(payload.get("prompt", "")))
        out = _workdir() / f"{task_id}.png"
        frame.save(out)
        if spec.kind == "video":
            video = out.with_suffix(".mp4")
            try:
                _video(out, video)
            finally:
                out.unlink(missing_ok=True)
        return task_id

    def status(self, task_id: str) -> TaskState:
        found = sorted(_workdir().glob(f"{task_id}.*"))
        if not found:
            return TaskState(state="failed", error="Fake AI: result went missing")
        return TaskState(state="done", urls=[f"{_SCHEME}{found[0].name}"])

    def download(self, url: str, dest: Path) -> Path:
        shutil.move(_workdir() / url.removeprefix(_SCHEME), dest)
        return dest
