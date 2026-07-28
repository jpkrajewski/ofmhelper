"""
Sync HTTP client for a ComfyUI server (the RunPod pod, or a local instance).

Talks ComfyUI's own API -- ``POST /prompt`` -> poll ``GET /history/{id}`` ->
``GET /view`` -- not RunPod's serverless ``/run`` + ``/status``. The pod is a
*Pod*, so ComfyUI is reached directly over its HTTP proxy and no RunPod API
key is involved.

Mirrors the conventions in aigenproviders/kaiai/client.py: requests-based,
synchronous, ``.from_env()`` constructor, plain dicts for payloads, and
stdlib exceptions rather than a bespoke error hierarchy.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import requests

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_HTTP_BAD_REQUEST = 400
# /prompt validation failures come back as 400 with a JSON body; raise_for_status
# alone would discard node_errors, which is the only part that says what broke.
_POLL_BACKOFF = 1.5
_POLL_INTERVAL_MAX_S = 10.0


class ComfyUIClient:
    """One ComfyUI server. Construct per base URL."""

    def __init__(
        self,
        base_url: str,
        *,
        out_dir: str | Path = "output",
        timeout_s: float = 600.0,
        poll_interval_s: float = 1.5,
        request_timeout_s: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.out_dir = Path(out_dir)
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self.request_timeout_s = request_timeout_s
        # ComfyUI keys its websocket/progress state by client_id; a stable one
        # per client keeps this process's prompts distinguishable in the UI.
        self.client_id = str(uuid.uuid4())

    @classmethod
    def from_env(cls) -> ComfyUIClient:
        cfg = settings.runpod
        return cls(
            cfg.comfy_base_url,
            out_dir=cfg.output_dir,
            timeout_s=cfg.timeout_s,
            poll_interval_s=cfg.poll_interval_s,
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    # ------------------------------------------------------------------
    # server introspection
    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        r = requests.get(self._url("/system_stats"), timeout=self.request_timeout_s)
        r.raise_for_status()
        return r.json()

    def object_info(self, node_class: str | None = None) -> dict[str, Any]:
        path = f"/object_info/{node_class}" if node_class else "/object_info"
        r = requests.get(self._url(path), timeout=self.request_timeout_s)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # inputs
    # ------------------------------------------------------------------
    def upload_image(self, path: str | Path, *, subfolder: str = "") -> str:
        """Upload a local image to the server's input dir.

        Returns the name to put in a LoadImage node -- which is not always the
        name uploaded, since ComfyUI renames on collision.
        """
        src = Path(path)
        if not src.is_file():
            msg = f"image not found: {src}"
            raise FileNotFoundError(msg)
        data = {"overwrite": "false"}
        if subfolder:
            data["subfolder"] = subfolder
        with src.open("rb") as fh:
            r = requests.post(
                self._url("/upload/image"),
                files={"image": (src.name, fh)},
                data=data,
                timeout=self.request_timeout_s,
            )
        r.raise_for_status()
        body = r.json()
        name = body["name"]
        if body.get("subfolder"):
            name = f"{body['subfolder']}/{name}"
        logger.info("uploaded %s -> %s", src, name)
        return name

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def submit(self, graph: dict[str, Any]) -> str:
        """Queue a graph. Returns the prompt_id."""
        r = requests.post(
            self._url("/prompt"),
            json={"prompt": graph, "client_id": self.client_id},
            timeout=self.request_timeout_s,
        )
        if r.status_code >= _HTTP_BAD_REQUEST:
            raise RuntimeError(_format_prompt_error(r))
        prompt_id = r.json()["prompt_id"]
        logger.info("queued prompt %s", prompt_id)
        return prompt_id

    def wait(self, prompt_id: str, *, timeout_s: float | None = None) -> dict[str, Any]:
        """Block until the prompt leaves the queue. Returns its history entry."""
        deadline = time.time() + (
            timeout_s if timeout_s is not None else self.timeout_s
        )
        interval = self.poll_interval_s
        while time.time() < deadline:
            r = requests.get(
                self._url(f"/history/{prompt_id}"), timeout=self.request_timeout_s
            )
            r.raise_for_status()
            entry = r.json().get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise RuntimeError(_format_history_error(prompt_id, entry))
                # completed=False with no error means it is still running.
                if status.get("completed", True):
                    return entry
            time.sleep(interval)
            interval = min(interval * _POLL_BACKOFF, _POLL_INTERVAL_MAX_S)
        msg = f"prompt {prompt_id} did not finish within {timeout_s or self.timeout_s}s"
        raise TimeoutError(msg)

    @staticmethod
    def outputs(history: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten a history entry into image refs, in node order.

        Saved images (SaveImage / WAS "Image Save") win over PreviewImage's
        ``type: temp`` scratch files, so a graph containing both does not
        return each result twice. But a graph whose only sink is a
        PreviewImage -- which several of these workflows are -- still returns
        its images rather than nothing.
        """
        saved, temp = [], []
        for node_out in (history.get("outputs") or {}).values():
            for image in node_out.get("images") or []:
                (temp if image.get("type") == "temp" else saved).append(image)
        return saved or temp

    def download(self, ref: dict[str, Any], dest_dir: str | Path | None = None) -> Path:
        dest = Path(dest_dir) if dest_dir is not None else self.out_dir
        dest.mkdir(parents=True, exist_ok=True)
        r = requests.get(
            self._url("/view"),
            params={
                "filename": ref["filename"],
                "subfolder": ref.get("subfolder", ""),
                "type": ref.get("type", "output"),
            },
            timeout=self.request_timeout_s,
        )
        r.raise_for_status()
        target = dest / ref["filename"]
        target.write_bytes(r.content)
        logger.info("downloaded %s (%d bytes)", target, len(r.content))
        return target

    def run(
        self,
        graph: dict[str, Any],
        *,
        out_dir: str | Path | None = None,
        timeout_s: float | None = None,
    ) -> list[Path]:
        """submit -> wait -> download. The one call wrappers actually use."""
        prompt_id = self.submit(graph)
        history = self.wait(prompt_id, timeout_s=timeout_s)
        refs = self.outputs(history)
        if not refs:
            logger.warning("prompt %s produced no saved images", prompt_id)
        return [self.download(ref, out_dir) for ref in refs]


def _format_prompt_error(response: requests.Response) -> str:
    """Turn a /prompt 400 into something that names the offending node."""
    try:
        body = response.json()
    except ValueError:
        return f"POST /prompt failed ({response.status_code}): {response.text[:500]}"

    parts = [f"POST /prompt failed ({response.status_code})"]
    error = body.get("error") or {}
    if error:
        parts.append(f"{error.get('type', '?')}: {error.get('message', '')}")
    for node_id, node_err in (body.get("node_errors") or {}).items():
        parts.extend(
            f"  node {node_id} ({node_err.get('class_type', '?')}): "
            f"{detail.get('message', '')} -- {detail.get('details', '')}"
            for detail in node_err.get("errors") or []
        )
    return "\n".join(parts)


def _format_history_error(prompt_id: str, entry: dict[str, Any]) -> str:
    parts = [f"prompt {prompt_id} failed"]
    for message in (entry.get("status") or {}).get("messages") or []:
        # messages are ["execution_error", {...}] pairs
        if len(message) == 2 and message[0] == "execution_error":  # noqa: PLR2004
            detail = message[1]
            parts.append(
                f"  {detail.get('node_type', '?')} "
                f"(node {detail.get('node_id', '?')}): "
                f"{detail.get('exception_type', '')}: "
                f"{detail.get('exception_message', '')}"
            )
    return "\n".join(parts)
