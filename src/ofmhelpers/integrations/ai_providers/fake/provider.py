# Copyright (C) 2026 jpkrajewski

import subprocess
import time
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.base import (
    Provider,
    TaskState,
    register_provider,
)
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderGenerationError,
    ProviderInputError,
)
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

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
        (24, 60),
        "(this is a placeholder, not a real generation)",
        fill=(190, 190, 220),
    )
    draw.multiline_text((24, 110), prompt[:500], fill=(255, 255, 255))
    return img


def _video(frame: Path, out: Path) -> None:
    command = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(frame), "-t", "1",
        "-vf", "scale=640:480", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ]  # fmt: skip
    try:
        subprocess.run(command, capture_output=True, check=True)  # noqa: S603
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
    needs_key = True

    @classmethod
    def default_key(cls) -> str | None:
        return None

    def upload(self, path: Path, max_bytes: int | None = None) -> str:  # noqa: ARG002
        return f"{_SCHEME}{path}"

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        del idempotency_key
        time.sleep(float(payload.get("delay") or 0))  # type: ignore[arg-type]
        if payload.get("outcome") == "error":
            raise ProviderGenerationError(
                str(payload.get("error_message") or "Fake AI: simulated failure"),
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
        (_workdir() / url.removeprefix(_SCHEME)).move(dest)
        return dest
