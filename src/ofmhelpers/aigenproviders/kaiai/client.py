import json
import os
import pathlib
import time

import requests

from ofmhelpers.aigenproviders.kaiai.upload_cache import upload_cache


class KieAIClient:
    """
    OOP wrapper around the kie.ai Market API (image/video generation
    aggregator). API key and every path/URL constant are injected via
    the constructor instead of living at module scope, so multiple
    clients (different providers/keys/output dirs) can coexist safely
    inside an aggregator process.
    """

    # How long resume_pending keeps retrying a pending task before writing
    # it off. kie.ai's result URLs are only reliably valid ~24h, so anything
    # older than this is unrecoverable anyway.
    RESUME_MAX_AGE_S = 48 * 3600

    def __init__(
        self,
        api_key: str,
        jobs_base: str = "https://api.kie.ai/api/v1/jobs",
        upload_base: str = "https://kieai.redpandaai.co",
        out_dir: str | pathlib.Path = "./out",
        task_log: str | pathlib.Path = "./tasks.jsonl",
        completions_log: str | pathlib.Path = "./completions.jsonl",
        resolved_log: str | pathlib.Path = "./resolved.jsonl",
    ) -> None:
        self.api_key = api_key
        self.JOBS_BASE = jobs_base
        self.UPLOAD_BASE = upload_base
        self.HEADERS = {"Authorization": f"Bearer {self.api_key}"}

        self.OUT_DIR = pathlib.Path(out_dir)
        self.TASK_LOG = pathlib.Path(task_log)
        self.COMPLETIONS_LOG = pathlib.Path(completions_log)
        self.RESOLVED_LOG = pathlib.Path(resolved_log)

        self.OUT_DIR.mkdir(parents=True, exist_ok=True)
        self.TASK_LOG.parent.mkdir(parents=True, exist_ok=True)
        self.COMPLETIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        self.RESOLVED_LOG.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # internal logging helper
    # ------------------------------------------------------------------
    def _log_task(self, task_id: str, model: str, prompt: str) -> None:
        with open(self.TASK_LOG, "a") as f:
            f.write(
                json.dumps(
                    {
                        "taskId": task_id,
                        "model": model,
                        "prompt": prompt,
                        "createdAt": time.time(),
                    }
                )
                + "\n"
            )

    # ------------------------------------------------------------------
    # File upload - only needed if a reference image/video/audio is a
    # LOCAL file. kie.ai's input fields (image_input, first_frame_url,
    # reference_*_urls) take hosted URLs, not raw bytes, so local files
    # go through this first.
    #
    # Reference files get reused across many generations (the /generate
    # reuse pickers exist specifically for this), so this checks
    # upload_cache before re-uploading the same bytes to kie.ai yet again --
    # a cache hit is still confirmed live (kie.ai's tempfile host doesn't
    # guarantee to keep files forever) before being trusted.
    # ------------------------------------------------------------------
    def upload_local_file(self, path: str, upload_path: str = "refs") -> str:
        cached_url = upload_cache.get(self.api_key, path)
        if cached_url is not None:
            if self._remote_file_exists(cached_url):
                print(f"[upload] cache hit for {path} -> {cached_url}", flush=True)
                return cached_url
            print(
                f"[upload] cached url for {path} no longer resolves, re-uploading",
                flush=True,
            )
            upload_cache.discard(self.api_key, path)

        print(f"[upload] starting {path} ...", flush=True)
        with open(path, "rb") as fh:
            r = requests.post(
                f"{self.UPLOAD_BASE}/api/file-stream-upload",
                headers=self.HEADERS,
                files={"file": fh},
                data={"uploadPath": upload_path, "fileName": pathlib.Path(path).name},
                timeout=900,
            )
        r.raise_for_status()
        if not r.json().get("success"):
            raise Exception("Wrong API Key")

        url = r.json()["data"]["downloadUrl"]
        print(f"[upload] done {path} -> {url}", flush=True)
        upload_cache.put(self.api_key, path, url)
        return url

    def _remote_file_exists(self, url: str) -> bool:
        """Best-effort existence check for a previously-uploaded file's
        hosted URL. Fails closed: any error/exception is treated as "not
        there" so the caller falls back to a fresh upload rather than
        risking a broken reference being handed to kie.ai."""
        try:
            r = requests.head(url, timeout=10, allow_redirects=True)
            return r.status_code < 400
        except requests.RequestException:
            return False

    # ------------------------------------------------------------------
    # Core async task lifecycle - shared by every Market model, including
    # both nano-banana-pro and bytedance/seedance-2.
    # ------------------------------------------------------------------
    def create_task(
        self, model: str, input_payload: dict, callback_url: str | None = None
    ) -> str:
        body = {"model": model, "input": input_payload}
        if callback_url:
            body["callBackUrl"] = (
                callback_url  # recommended for prod; skips polling entirely
            )
        r = requests.post(
            f"{self.JOBS_BASE}/createTask", headers=self.HEADERS, json=body, timeout=30
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 200:
            raise RuntimeError(f"createTask rejected: {data}")
        task_id = data["data"]["taskId"]
        self._log_task(task_id, model, input_payload.get("prompt", ""))
        return task_id

    def poll_task(
        self,
        task_id: str,
        timeout_s: int = 1000,
        interval: float = 2.5,
        max_interval: float = 20.0,
    ) -> list[str]:
        """Poll recordInfo with exponential backoff until success/fail.
        kie.ai caps sane polling at 10-15 min; 900s = 15 min default."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            r = requests.get(
                f"{self.JOBS_BASE}/recordInfo",
                headers=self.HEADERS,
                params={"taskId": task_id},
                timeout=30,
            )
            r.raise_for_status()
            d = r.json()["data"]
            state = d["state"]  # waiting -> queuing -> generating -> success/fail
            print(d)

            if state == "success":
                with open(self.COMPLETIONS_LOG, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "taskId": task_id,
                                "credits": d.get("creditsConsumed"),
                                "costTimeMs": d.get("costTime"),
                            }
                        )
                        + "\n"
                    )
                return json.loads(d["resultJson"])["resultUrls"]

            if state == "fail":
                raise RuntimeError(f"{task_id} failed: {d.get('failMsg')}")

            time.sleep(interval)
            interval = min(interval * 1.5, max_interval)

        raise TimeoutError(
            f"kie.ai is still generating (task {task_id}, waited {timeout_s}s). "
            f"No action needed -- the background recovery sweeper will download "
            f"it to the server automatically once it finishes."
        )

    # ------------------------------------------------------------------
    # Single-shot status check - one recordInfo call, no waiting. Used by
    # the recovery sweeper so a sweep over many pending tasks never blocks.
    # ------------------------------------------------------------------
    def check_task(self, task_id: str) -> tuple[str, list[str] | str | None]:
        """Returns (state, payload): ("success", [urls]) / ("fail", failMsg) /
        ("unknown", api message -- e.g. wrong key or unknown task) / any
        in-flight state ("waiting"/"queuing"/"generating", None)."""
        r = requests.get(
            f"{self.JOBS_BASE}/recordInfo",
            headers=self.HEADERS,
            params={"taskId": task_id},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        data = body.get("data") or {}
        state = data.get("state")
        if state == "success":
            return "success", json.loads(data["resultJson"])["resultUrls"]
        if state == "fail":
            return "fail", data.get("failMsg")
        if state is None:
            return "unknown", body.get("msg")
        return state, None

    def download_urls(
        self, urls: list[str], task_id: str, ext: str
    ) -> list[pathlib.Path]:
        """Pull results down NOW. kie.ai's best-practice note says result URLs are
        only reliably valid ~24h, even though the underlying file may live 14 days."""
        saved = []
        for i, url in enumerate(urls):
            # Only disambiguate with an index suffix when there's more than
            # one result to save -- every model here returns exactly one
            # today, so this is normally just "{task_id}.{ext}".
            suffix = f"_{i}" if len(urls) > 1 else ""
            out = self.OUT_DIR / f"{task_id}{suffix}.{ext}"
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(out, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
            saved.append(out)
        return saved

    # ------------------------------------------------------------------
    # Nano Banana Pro - image generation
    # model = "nano-banana-pro", resolution uses image tiers (1K/2K/4K),
    # NOT the 480p/720p/1080p used by video models.
    # ------------------------------------------------------------------
    def generate_image_nbp(
        self,
        prompt: str,
        image_input: list[str] | None = None,
        aspect_ratio: str = "1:1",
        resolution: str = "1K",
        output_format: str = "png",
        callback_url: str | None = None,
    ) -> pathlib.Path:
        payload = {
            "prompt": prompt,
            "image_input": image_input or [],
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "output_format": output_format,
        }
        task_id = self.create_task("nano-banana-pro", payload, callback_url)
        urls = self.poll_task(task_id)
        return self.download_urls(urls, task_id, output_format)[0]

    # ------------------------------------------------------------------
    # Seedance 2.0 - video generation
    # model defaults to "bytedance/seedance-2" but any Seedance 2 variant
    # ("bytedance/seedance-2", "bytedance/seedance-2-fast",
    # "bytedance/seedance-2-mini") can be passed in. First/last-frame and
    # multimodal reference are mutually exclusive per kie.ai's docs -- this
    # picks one or the other.
    # ------------------------------------------------------------------
    SEEDANCE2_MODELS = (
        "bytedance/seedance-2",
        "bytedance/seedance-2-fast",
        "bytedance/seedance-2-mini",
    )

    def generate_video_seedance2(
        self,
        prompt: str,
        model: str = "bytedance/seedance-2",
        resolution: str = "720p",
        aspect_ratio: str = "16:9",
        duration: int = 10,
        generate_audio: bool = True,
        first_frame_url: str | None = None,
        last_frame_url: str | None = None,
        reference_image_urls: list[str] | None = None,
        reference_video_urls: list[str] | None = None,
        reference_audio_urls: list[str] | None = None,
        callback_url: str | None = None,
    ) -> pathlib.Path:
        if model not in self.SEEDANCE2_MODELS:
            raise ValueError(
                f"Unsupported model {model!r}; expected one of {self.SEEDANCE2_MODELS}"
            )

        payload = {
            "prompt": prompt,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "duration": duration,
            "generate_audio": generate_audio,
            "web_search": False,
            "return_last_frame": False,
            "nsfw_checker": False,
        }
        if first_frame_url:
            payload["first_frame_url"] = first_frame_url
            if last_frame_url:
                payload["last_frame_url"] = last_frame_url
        elif reference_image_urls or reference_video_urls or reference_audio_urls:
            if reference_image_urls:
                payload["reference_image_urls"] = reference_image_urls
            if reference_video_urls:
                payload["reference_video_urls"] = reference_video_urls
            if reference_audio_urls:
                payload["reference_audio_urls"] = reference_audio_urls

        task_id = self.create_task(model, payload, callback_url)
        urls = self.poll_task(task_id, timeout_s=1800)
        return self.download_urls(urls, task_id, "mp4")[0]

    # ------------------------------------------------------------------
    # Kling 3.0 - video generation
    # model = "kling-3.0/video". Unlike Seedance, Kling 3.0 takes a plain
    # list of reference image_urls (not first/last frame split), plus
    # optional multi-shot storyboarding (multi_prompt) and @element_name
    # references resolved via kling_elements. duration is capped at 15s
    # total across all shots per kie.ai's docs.
    # ------------------------------------------------------------------
    KLING3_MODES = ("std", "pro", "4K")

    def generate_video_kling3(
        self,
        prompt: str | None = None,
        image_urls: list[str] | None = None,
        mode: str = "pro",
        aspect_ratio: str = "16:9",
        duration: str = "5",
        sound: bool = True,
        multi_shots: bool = False,
        multi_prompt: list[dict] | None = None,
        kling_elements: list[dict] | None = None,
        callback_url: str | None = None,
    ) -> pathlib.Path:
        if mode not in self.KLING3_MODES:
            raise ValueError(
                f"Unsupported mode {mode!r}; expected one of {self.KLING3_MODES}"
            )

        payload: dict = {
            "mode": mode,
            "aspect_ratio": aspect_ratio,
            "duration": duration,
            "sound": sound,
            "multi_shots": multi_shots,
        }
        if image_urls:
            payload["image_urls"] = image_urls
        if prompt:
            payload["prompt"] = prompt
        if multi_shots and multi_prompt:
            payload["multi_prompt"] = multi_prompt
        if kling_elements:
            payload["kling_elements"] = kling_elements

        task_id = self.create_task("kling-3.0/video", payload, callback_url)
        urls = self.poll_task(task_id, timeout_s=1800)
        return self.download_urls(urls, task_id, "mp4")[0]

    # ------------------------------------------------------------------
    # Crash/timeout recovery - safe to re-run any time; the web app's
    # background sweeper calls this every few minutes. Sweeps tasks.jsonl
    # for anything created but never downloaded (poll timed out, server
    # restarted mid-generation, etc.) and finishes the job. Terminal
    # outcomes are appended to RESOLVED_LOG so dead tasks are never
    # re-checked on later sweeps.
    # ------------------------------------------------------------------
    def _load_resolved(self) -> set[str]:
        if not self.RESOLVED_LOG.exists():
            return set()
        ids = set()
        for line in self.RESOLVED_LOG.read_text().splitlines():
            try:
                ids.add(json.loads(line)["taskId"])
            except (json.JSONDecodeError, KeyError):
                continue
        return ids

    def _mark_resolved(self, task_id: str, outcome: str) -> None:
        with open(self.RESOLVED_LOG, "a") as f:
            f.write(
                json.dumps(
                    {"taskId": task_id, "outcome": outcome, "resolvedAt": time.time()}
                )
                + "\n"
            )

    def resume_pending(self) -> list[dict]:
        recovered = []
        if not self.TASK_LOG.exists():
            return recovered
        resolved = self._load_resolved()

        for line in self.TASK_LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = rec["taskId"]

            if tid in resolved:
                continue
            # "{tid}.ext" for a single result, "{tid}_0.ext" etc. for several.
            if any(self.OUT_DIR.glob(f"{tid}.*")) or any(self.OUT_DIR.glob(f"{tid}_*")):
                self._mark_resolved(tid, "already-downloaded")
                continue
            if time.time() - rec.get("createdAt", 0) > self.RESUME_MAX_AGE_S:
                # result URLs are long dead by now -- stop checking forever
                self._mark_resolved(tid, "expired")
                continue

            try:
                state, payload = self.check_task(tid)
            except Exception as exc:  # network blip etc. -- next sweep retries
                print(f"[recovery] {tid}: status check failed: {exc}", flush=True)
                continue

            if state == "success":
                ext = (
                    "mp4"
                    if any(k in rec.get("model", "") for k in ("seedance", "kling"))
                    else "png"
                )
                try:
                    self.download_urls(payload, tid, ext)
                except Exception as exc:  # leave pending, next sweep retries
                    print(f"[recovery] {tid}: download failed: {exc}", flush=True)
                    continue
                self._mark_resolved(tid, "downloaded")
                recovered.append({"taskId": tid, "outcome": "downloaded"})
                print(f"[recovery] downloaded {tid}", flush=True)
            elif state == "fail":
                self._mark_resolved(tid, "failed")
                print(f"[recovery] {tid} failed on kie.ai: {payload}", flush=True)
            # "unknown" (task belongs to a different key, etc.) and in-flight
            # states are left pending -- another key or a later sweep gets it,
            # and the age cutoff above guarantees it can't linger forever.
        return recovered

    @classmethod
    def from_env(cls, api_key):
        return cls(
            api_key=api_key,
            out_dir=os.getenv("OFM_KIEAI_OUT_DIR", "/app/kieai_out"),
            task_log=os.getenv("OFM_KIEAI_TASK_LOG", "/app/kieai_out/tasks.jsonl"),
            completions_log=os.getenv(
                "OFM_KIEAI_COMPLETIONS_LOG", "/app/kieai_out/completions.jsonl"
            ),
            resolved_log=os.getenv(
                "OFM_KIEAI_RESOLVED_LOG", "/app/kieai_out/resolved.jsonl"
            ),
        )
