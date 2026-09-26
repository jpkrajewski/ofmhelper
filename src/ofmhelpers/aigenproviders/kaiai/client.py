import json
import pathlib
import time
from collections.abc import Callable

import requests

from ofmhelpers.aigenproviders.kaiai.types import (
    ADAPTIVE_ASPECT_RATIO,
    IMAGE_EXT,
    SEEDREAM5_MODELS,
    VIDEO_EXT,
    VIDEO_MODELS,
    ImageBackground,
    ImageResolution,
    KieModel,
    Kling3Mode,
    MinimaxH3Resolution,
    Seedance2Model,
    SeedanceResolution,
    Seedream5Quality,
    Seedream5Variant,
    Seedream45Quality,
    SeedreamOutputFormat,
    Wan3Resolution,
)
from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


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
    RESUME_MAX_AGE_S = settings.kieai.resume_max_age_s

    # kie.ai signals success in a JSON body field, not the HTTP status.
    _HTTP_ERROR = 400
    _API_OK = 200

    def __init__(
        self,
        api_key: str,
        jobs_base: str | None = None,
        upload_base: str | None = None,
        out_dir: str | pathlib.Path = "./out",
        task_log: str | pathlib.Path = "./tasks.jsonl",
        completions_log: str | pathlib.Path = "./completions.jsonl",
        resolved_log: str | pathlib.Path = "./resolved.jsonl",
    ) -> None:
        self.api_key = api_key
        # Read here rather than as a default expression: a default is bound at
        # import, which would capture the env before a test override lands.
        self.JOBS_BASE = jobs_base or settings.kieai.jobs_base
        self.UPLOAD_BASE = upload_base or settings.kieai.upload_base
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
        with self.TASK_LOG.open("a") as f:
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
    # reuse pickers exist specifically for this), so an already-uploaded URL
    # is cached in Redis before re-uploading the same bytes to kie.ai yet
    # again -- a cache hit is still confirmed live (kie.ai's tempfile host
    # doesn't guarantee to keep files forever) before being trusted.
    #
    # Keyed by (api_key, local path), not path alone: kie.ai namespaces
    # uploads per account (the "kieai/<account-id>/refs/..." segment in every
    # downloadUrl), so a URL uploaded under one API key is not guaranteed
    # valid -- or even the right file -- under a different key, and this app
    # hands out two (admin/VA) that reference the same local files.
    #
    # In Redis rather than in-process so the API, the worker and every uvicorn
    # worker share one answer. Pure optimisation: a broker outage reports a
    # miss and the file is simply re-uploaded.
    # ------------------------------------------------------------------
    def _upload_cache_key(self, path: str) -> str:
        return f"kieai:upload:{self.api_key}:{path}"

    def upload_local_file(self, path: str, upload_path: str = "refs") -> str:
        cache_key = self._upload_cache_key(path)
        cached_url = get_text(cache_key)
        if cached_url is not None:
            if self._remote_file_exists(cached_url):
                logger.info("upload cache hit for %s -> %s", path, cached_url)
                return cached_url
            logger.info("cached url for %s no longer resolves, re-uploading", path)
            delete_text(cache_key)

        logger.info("upload starting: %s", path)
        with pathlib.Path(path).open("rb") as fh:
            r = requests.post(
                f"{self.UPLOAD_BASE}/api/file-stream-upload",
                headers=self.HEADERS,
                files={"file": fh},
                data={"uploadPath": upload_path, "fileName": pathlib.Path(path).name},
                timeout=settings.kieai.upload_timeout_s,
            )
        r.raise_for_status()
        if not r.json().get("success"):
            msg = "Wrong API Key"
            raise RuntimeError(msg)

        url = r.json()["data"]["downloadUrl"]
        logger.info("upload done: %s -> %s", path, url)
        set_text(cache_key, url, settings.kieai.upload_cache_ttl_s)
        return url

    def _remote_file_exists(self, url: str) -> bool:
        """Best-effort existence check for a previously-uploaded file's
        hosted URL. Fails closed: any error/exception is treated as "not
        there" so the caller falls back to a fresh upload rather than
        risking a broken reference being handed to kie.ai."""
        try:
            r = requests.head(
                url,
                timeout=settings.kieai.remote_check_timeout_s,
                allow_redirects=True,
            )
        except requests.RequestException:
            return False
        else:
            return r.status_code < self._HTTP_ERROR

    # ------------------------------------------------------------------
    # Core async task lifecycle - shared by every Market model, including
    # both nano-banana-pro and bytedance/seedance-2.
    # ------------------------------------------------------------------
    def create_task(
        self, model: str, input_payload: dict, callback_url: str | None = None
    ) -> str:
        body: dict = {"model": model, "input": input_payload}
        if callback_url:
            body["callBackUrl"] = (
                callback_url  # recommended for prod; skips polling entirely
            )
        r = requests.post(
            f"{self.JOBS_BASE}/createTask",
            headers=self.HEADERS,
            json=body,
            timeout=settings.kieai.request_timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != self._API_OK:
            msg = f"createTask rejected: {data}"
            raise RuntimeError(msg)
        task_id = data["data"]["taskId"]
        self._log_task(task_id, model, input_payload.get("prompt", ""))
        return task_id

    def poll_task(
        self,
        task_id: str,
        timeout_s: int | None = None,
        interval: float = 2.5,
        max_interval: float = 20.0,
    ) -> list[str]:
        """Poll recordInfo with exponential backoff until success/fail.
        kie.ai caps sane polling at 10-15 min."""
        timeout_s = (
            timeout_s if timeout_s is not None else settings.kieai.poll_timeout_s
        )
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            r = requests.get(
                f"{self.JOBS_BASE}/recordInfo",
                headers=self.HEADERS,
                params={"taskId": task_id},
                timeout=settings.kieai.request_timeout_s,
            )
            r.raise_for_status()
            d = r.json()["data"]
            state = d["state"]  # waiting -> queuing -> generating -> success/fail
            # Polled every ~2.5s for up to 15 min -- debug, or one generation
            # buries every other line in the container log.
            logger.debug("poll %s: state=%s payload=%s", task_id, state, d)

            if state == "success":
                with self.COMPLETIONS_LOG.open("a") as f:
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
                msg = f"{task_id} failed: {d.get('failMsg')}"
                raise RuntimeError(msg)

            time.sleep(interval)
            interval = min(interval * 1.5, max_interval)

        msg = (
            f"kie.ai is still generating (task {task_id}, waited {timeout_s}s). "
            f"No action needed -- the background recovery sweeper will download "
            f"it to the server automatically once it finishes."
        )
        raise TimeoutError(msg)

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
            timeout=settings.kieai.request_timeout_s,
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
            with requests.get(
                url, stream=True, timeout=settings.kieai.download_timeout_s
            ) as r:
                r.raise_for_status()
                with pathlib.Path(out).open("wb") as f:
                    f.writelines(r.iter_content(1 << 16))
            saved.append(out)
        return saved

    # ------------------------------------------------------------------
    # Per-model wrappers. Each one only builds its payload; the shared
    # create -> poll -> preview -> download tail is _generate. The
    # on_result_urls hook fires as soon as kie.ai's hosted result is live, so
    # a caller (the web app) can show it without blocking on the download.
    # ------------------------------------------------------------------
    def _generate(
        self,
        model: KieModel,
        payload: dict,
        callback_url: str | None,
        on_result_urls: Callable[[list[str]], None] | None,
        ext: str = IMAGE_EXT,
    ) -> pathlib.Path:
        task_id = self.create_task(model, payload, callback_url)
        if model in VIDEO_MODELS:
            urls = self.poll_task(
                task_id, timeout_s=settings.kieai.video_poll_timeout_s
            )
        else:
            urls = self.poll_task(task_id)
        if on_result_urls:
            on_result_urls(urls)
        return self.download_urls(urls, task_id, ext)[0]

    @staticmethod
    def _reference_inputs(
        reference_image_urls: list[str] | None,
        reference_video_urls: list[str] | None,
        reference_audio_urls: list[str] | None,
    ) -> dict:
        """The reference_* keys that are non-empty -- kie.ai rejects empty lists."""
        refs = {
            "reference_image_urls": reference_image_urls,
            "reference_video_urls": reference_video_urls,
            "reference_audio_urls": reference_audio_urls,
        }
        return {k: v for k, v in refs.items() if v}

    @classmethod
    def _frame_or_reference_inputs(
        cls,
        first_frame_url: str | None,
        last_frame_url: str | None,
        reference_image_urls: list[str] | None,
        reference_video_urls: list[str] | None,
        reference_audio_urls: list[str] | None,
    ) -> dict:
        """First/last frame and reference_* lists are mutually exclusive on
        the omni-reference models (Seedance, Wan) -- frames win."""
        if first_frame_url:
            frames = {"first_frame_url": first_frame_url}
            if last_frame_url:
                frames["last_frame_url"] = last_frame_url
            return frames
        return cls._reference_inputs(
            reference_image_urls, reference_video_urls, reference_audio_urls
        )

    # Nano Banana Pro - resolution uses image tiers (1K/2K/4K), NOT the
    # 480p/720p/1080p used by video models.
    def generate_image_nbp(
        self,
        prompt: str,
        image_input: list[str] | None = None,
        aspect_ratio: str = "1:1",
        resolution: str = ImageResolution.K1,
        output_format: str = IMAGE_EXT,
        callback_url: str | None = None,
        on_result_urls: Callable[[list[str]], None] | None = None,
    ) -> pathlib.Path:
        payload = {
            "prompt": prompt,
            "image_input": image_input or [],
            "aspect_ratio": aspect_ratio,
            "resolution": ImageResolution(resolution),
            "output_format": output_format,
        }
        return self._generate(
            KieModel.NANO_BANANA_PRO,
            payload,
            callback_url,
            on_result_urls,
            output_format,
        )

    # Seedream 4.5 - text-to-image, or the edit model when image_urls are
    # given. Takes no output_format.
    def generate_image_seedream45(
        self,
        prompt: str,
        image_urls: list[str] | None = None,
        aspect_ratio: str = "1:1",
        quality: str = Seedream45Quality.BASIC,
        callback_url: str | None = None,
        on_result_urls: Callable[[list[str]], None] | None = None,
    ) -> pathlib.Path:
        payload: dict = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "quality": Seedream45Quality(quality),
            "nsfw_checker": False,
        }
        model = KieModel.SEEDREAM_4_5_TEXT
        if image_urls:
            payload["image_urls"] = image_urls
            model = KieModel.SEEDREAM_4_5_EDIT
        return self._generate(model, payload, callback_url, on_result_urls)

    # Seedream 5.0 - Lite or Pro, each text-to-image or image-to-image by
    # whether image_urls are given. "ultra" quality exists on Lite only.
    def generate_image_seedream5(
        self,
        prompt: str,
        variant: str = Seedream5Variant.PRO,
        image_urls: list[str] | None = None,
        aspect_ratio: str = "1:1",
        quality: str = Seedream5Quality.BASIC,
        output_format: str = SeedreamOutputFormat.PNG,
        callback_url: str | None = None,
        on_result_urls: Callable[[list[str]], None] | None = None,
    ) -> pathlib.Path:
        tier = Seedream5Variant(variant)
        level = Seedream5Quality(quality)
        if level == Seedream5Quality.ULTRA and tier != Seedream5Variant.LITE:
            msg = f"Seedream 5.0 {tier} has no 'ultra' quality (Lite only)"
            raise ValueError(msg)

        fmt = SeedreamOutputFormat(output_format)
        payload: dict = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "quality": level,
            "output_format": fmt,
            "nsfw_checker": False,
        }
        text_model, image_model = SEEDREAM5_MODELS[tier]
        model = text_model
        if image_urls:
            payload["image_urls"] = image_urls
            model = image_model
        return self._generate(model, payload, callback_url, on_result_urls, fmt)

    # GPT Image 2.5 Flare (ChatGPT image) - image-to-image only: at least one
    # input image is required. kie.ai renders the 27:16 / 16:27 / 9:8 / 8:9
    # aspect ratios at 1K only.
    def generate_image_gpt25_flare(
        self,
        prompt: str,
        input_urls: list[str],
        aspect_ratio: str = "auto",
        resolution: str = ImageResolution.K1,
        background: str = ImageBackground.AUTO,
        callback_url: str | None = None,
        on_result_urls: Callable[[list[str]], None] | None = None,
    ) -> pathlib.Path:
        if not input_urls:
            msg = "GPT Image 2.5 Flare is image-to-image: input_urls is required"
            raise ValueError(msg)
        payload = {
            "prompt": prompt,
            "input_urls": input_urls,
            "aspect_ratio": aspect_ratio,
            "resolution": ImageResolution(resolution),
            "background": ImageBackground(background),
        }
        return self._generate(
            KieModel.GPT_IMAGE_2_5_FLARE_IMAGE, payload, callback_url, on_result_urls
        )

    @classmethod
    def _seedance_payload(
        cls,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        duration: int,
        generate_audio: bool,
        first_frame_url: str | None,
        last_frame_url: str | None,
        reference_image_urls: list[str] | None,
        reference_video_urls: list[str] | None,
        reference_audio_urls: list[str] | None,
    ) -> dict:
        """Seedance 2.0 and 2.5 take the same input shape."""
        return {
            "prompt": prompt,
            "resolution": SeedanceResolution(resolution),
            "aspect_ratio": aspect_ratio,
            "duration": duration,
            "generate_audio": generate_audio,
            "web_search": False,
            "return_last_frame": False,
            "nsfw_checker": False,
            **cls._frame_or_reference_inputs(
                first_frame_url,
                last_frame_url,
                reference_image_urls,
                reference_video_urls,
                reference_audio_urls,
            ),
        }

    # Seedance 2.0 - standard / fast / mini tiers.
    def generate_video_seedance2(
        self,
        prompt: str,
        model: str = Seedance2Model.STANDARD,
        resolution: str = SeedanceResolution.P720,
        aspect_ratio: str = "16:9",
        duration: int = 10,
        generate_audio: bool = True,
        first_frame_url: str | None = None,
        last_frame_url: str | None = None,
        reference_image_urls: list[str] | None = None,
        reference_video_urls: list[str] | None = None,
        reference_audio_urls: list[str] | None = None,
        callback_url: str | None = None,
        on_result_urls: Callable[[list[str]], None] | None = None,
    ) -> pathlib.Path:
        payload = self._seedance_payload(
            prompt,
            resolution,
            aspect_ratio,
            duration,
            generate_audio,
            first_frame_url,
            last_frame_url,
            reference_image_urls,
            reference_video_urls,
            reference_audio_urls,
        )
        return self._generate(
            KieModel(Seedance2Model(model)),
            payload,
            callback_url,
            on_result_urls,
            VIDEO_EXT,
        )

    # Seedance 2.5 - 4-30s (or -1 for model-picked), adaptive aspect ratio.
    def generate_video_seedance25(
        self,
        prompt: str,
        resolution: str = SeedanceResolution.P720,
        aspect_ratio: str = ADAPTIVE_ASPECT_RATIO,
        duration: int = 5,
        generate_audio: bool = True,
        first_frame_url: str | None = None,
        last_frame_url: str | None = None,
        reference_image_urls: list[str] | None = None,
        reference_video_urls: list[str] | None = None,
        reference_audio_urls: list[str] | None = None,
        callback_url: str | None = None,
        on_result_urls: Callable[[list[str]], None] | None = None,
    ) -> pathlib.Path:
        payload = self._seedance_payload(
            prompt,
            resolution,
            aspect_ratio,
            duration,
            generate_audio,
            first_frame_url,
            last_frame_url,
            reference_image_urls,
            reference_video_urls,
            reference_audio_urls,
        )
        return self._generate(
            KieModel.SEEDANCE_2_5, payload, callback_url, on_result_urls, VIDEO_EXT
        )

    # Kling 3.0 - a plain list of reference image_urls (no first/last frame
    # split), plus optional multi-shot storyboarding (multi_prompt) and
    # @element_name references resolved via kling_elements. duration is
    # capped at 15s total across all shots per kie.ai's docs.
    def generate_video_kling3(
        self,
        prompt: str | None = None,
        image_urls: list[str] | None = None,
        mode: str = Kling3Mode.PRO,
        aspect_ratio: str = "16:9",
        duration: str = "5",
        sound: bool = True,
        multi_shots: bool = False,
        multi_prompt: list[dict] | None = None,
        kling_elements: list[dict] | None = None,
        callback_url: str | None = None,
        on_result_urls: Callable[[list[str]], None] | None = None,
    ) -> pathlib.Path:
        payload: dict = {
            "mode": Kling3Mode(mode),
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
        return self._generate(
            KieModel.KLING_3, payload, callback_url, on_result_urls, VIDEO_EXT
        )

    # Wan 3.0 - Alibaba's omni-reference model: first/last frames OR a mix of
    # reference images/videos/audio. Resolutions are upper-case ("1080P").
    def generate_video_wan3(
        self,
        prompt: str,
        resolution: str = Wan3Resolution.P1080,
        aspect_ratio: str = ADAPTIVE_ASPECT_RATIO,
        duration: int = 5,
        audio: bool = True,
        first_frame_url: str | None = None,
        last_frame_url: str | None = None,
        reference_image_urls: list[str] | None = None,
        reference_video_urls: list[str] | None = None,
        reference_audio_urls: list[str] | None = None,
        seed: int | None = None,
        callback_url: str | None = None,
        on_result_urls: Callable[[list[str]], None] | None = None,
    ) -> pathlib.Path:
        payload: dict = {
            "prompt": prompt,
            "resolution": Wan3Resolution(resolution),
            "aspect_ratio": aspect_ratio,
            "duration": duration,
            "audio": audio,
            "nsfw_checker": False,
            **self._frame_or_reference_inputs(
                first_frame_url,
                last_frame_url,
                reference_image_urls,
                reference_video_urls,
                reference_audio_urls,
            ),
        }
        if seed is not None:
            payload["seed"] = seed
        return self._generate(
            KieModel.WAN_3, payload, callback_url, on_result_urls, VIDEO_EXT
        )

    # MiniMax H3 - three kie.ai models picked by input: a frame ->
    # image-to-video (takes no aspect_ratio), reference_* lists ->
    # reference-to-video, neither -> text-to-video (rejects "adaptive").
    def generate_video_minimax_h3(
        self,
        prompt: str,
        resolution: str = MinimaxH3Resolution.K2,
        aspect_ratio: str = "16:9",
        duration: int = 6,
        first_frame_url: str | None = None,
        last_frame_url: str | None = None,
        reference_image_urls: list[str] | None = None,
        reference_video_urls: list[str] | None = None,
        reference_audio_urls: list[str] | None = None,
        callback_url: str | None = None,
        on_result_urls: Callable[[list[str]], None] | None = None,
    ) -> pathlib.Path:
        payload: dict = {
            "prompt": prompt,
            "resolution": MinimaxH3Resolution(resolution),
            "duration": duration,
        }
        references = self._reference_inputs(
            reference_image_urls, reference_video_urls, reference_audio_urls
        )
        if first_frame_url or last_frame_url:
            model = KieModel.MINIMAX_H3_IMAGE
            frames = {
                "first_frame_url": first_frame_url,
                "last_frame_url": last_frame_url,
            }
            payload.update({k: v for k, v in frames.items() if v})
        elif references:
            model = KieModel.MINIMAX_H3_REFERENCE
            payload.update(references, aspect_ratio=aspect_ratio)
        elif aspect_ratio == ADAPTIVE_ASPECT_RATIO:
            msg = "MiniMax H3 text-to-video needs a fixed aspect_ratio, not adaptive"
            raise ValueError(msg)
        else:
            model = KieModel.MINIMAX_H3_TEXT
            payload["aspect_ratio"] = aspect_ratio
        return self._generate(model, payload, callback_url, on_result_urls, VIDEO_EXT)

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
        with self.RESOLVED_LOG.open("a") as f:
            f.write(
                json.dumps(
                    {"taskId": task_id, "outcome": outcome, "resolvedAt": time.time()}
                )
                + "\n"
            )

    def resume_pending(self) -> list[dict]:
        recovered: list[dict] = []
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
            except Exception:  # network blip etc. -- next sweep retries
                logger.warning("recovery %s: status check failed", tid, exc_info=True)
                continue

            if state == "success" and isinstance(payload, list):
                ext = VIDEO_EXT if rec.get("model") in VIDEO_MODELS else IMAGE_EXT
                try:
                    self.download_urls(payload, tid, ext)
                except Exception:  # leave pending, next sweep retries
                    logger.warning("recovery %s: download failed", tid, exc_info=True)
                    continue
                self._mark_resolved(tid, "downloaded")
                recovered.append({"taskId": tid, "outcome": "downloaded"})
                logger.info("recovery downloaded %s", tid)
            elif state == "fail":
                self._mark_resolved(tid, "failed")
                logger.error("recovery %s failed on kie.ai: %s", tid, payload)
            # "unknown" (task belongs to a different key, etc.) and in-flight
            # states are left pending -- another key or a later sweep gets it,
            # and the age cutoff above guarantees it can't linger forever.
        return recovered

    @classmethod
    def from_env(cls, api_key):
        s = settings.kieai
        return cls(
            api_key=api_key,
            out_dir=s.out_dir,
            task_log=s.task_log,
            completions_log=s.completions_log,
            resolved_log=s.resolved_log,
        )
