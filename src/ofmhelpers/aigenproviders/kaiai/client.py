"""The kie.ai Market API: upload a reference file, create a task, read it.

Single calls only. Which model, with what input, comes from the catalog
(`catalog.json`); waiting, resuming and downloading are `runner.py`'s. Every
response is parsed into `models.py`, which also maps kie.ai's in-body error
codes onto the shared error types.
"""

import hashlib
import json
import pathlib
import time

import requests
from pydantic import BaseModel
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from ofmhelpers.aigenproviders.base import upload_lock
from ofmhelpers.aigenproviders.errors import ProviderUnavailableError
from ofmhelpers.aigenproviders.kaiai.models import (
    SUCCESS,
    KieCreateResponse,
    KieRecord,
    KieRecordResponse,
    KieUploadResponse,
    raise_for,
)
from ofmhelpers.aigenproviders.spec import Payload
from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound
from ofmhelpers.utils.image_fit import fitted

logger = get_logger(__name__)


def _session() -> requests.Session:
    """Retries connection errors and 429/5xx on reads only: a createTask
    that reached kie.ai and timed out must not be sent twice (it would be
    paid twice), so POSTs are never retried here."""
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return outbound.mount(session)


def _retry_after_s(response: requests.Response) -> float | None:
    """An HTTP answer's Retry-After in seconds, when it gives a number."""
    value = response.headers.get("Retry-After")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parsed[M: BaseModel](response: requests.Response, model: type[M]) -> M:
    """A 200 answer as `model`. One that is not JSON or not of that shape is
    kie.ai misbehaving, not the request: `ProviderUnavailableError`, so a
    poll keeps trying and a paid request stays remembered for recovery
    (an unparsed answer used to fail the job as non-resumable)."""
    try:
        return model.model_validate(response.json())
    except ValueError as exc:  # JSONDecodeError and ValidationError alike
        msg = f"kie.ai: unreadable answer ({exc.__class__.__name__})"
        raise ProviderUnavailableError(msg) from exc


class KieAIClient:
    """One kie.ai account. Paths and URLs are injected so tests and several
    keys can coexist in one process."""

    _HTTP_ERROR = 400

    def __init__(
        self,
        api_key: str,
        jobs_base: str | None = None,
        upload_base: str | None = None,
        out_dir: str | pathlib.Path = "./out",
        task_log: str | pathlib.Path = "./tasks.jsonl",
        completions_log: str | pathlib.Path = "./completions.jsonl",
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
        for path in (self.OUT_DIR, self.TASK_LOG.parent, self.COMPLETIONS_LOG.parent):
            path.mkdir(parents=True, exist_ok=True)
        self._http = _session()

    def _call(self, method: str, url: str, **kwargs) -> requests.Response:
        """One HTTP call; transport failures and HTTP errors become
        `ProviderUnavailableError` / the in-body code's error type."""
        try:
            response = self._http.request(method, url, headers=self.HEADERS, **kwargs)
        except requests.RequestException as exc:
            msg = f"kie.ai unreachable: {exc.__class__.__name__}"
            raise ProviderUnavailableError(msg) from exc
        if response.status_code >= self._HTTP_ERROR:
            # Normally kie.ai answers 200 with its code in the body; a real
            # HTTP error is mapped by its status the same way.
            raise_for(
                response.status_code, response.text[:200], _retry_after_s(response)
            )
        return response

    # ------------------------------------------------------------------
    # Audit logs: every task created and every credit spent, one JSON line
    # each, next to the results.
    # ------------------------------------------------------------------
    def _log_task(self, task_id: str, model: str, prompt: str, job_id: str) -> None:
        record = {
            "taskId": task_id,
            "model": model,
            "prompt": prompt,
            "createdAt": time.time(),
            "jobId": job_id,
        }
        with self.TASK_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def _log_completion(self, record: KieRecord) -> None:
        line = {
            "taskId": record.task_id,
            "credits": record.credits,
            "costTimeMs": record.cost_time_ms,
        }
        with self.COMPLETIONS_LOG.open("a") as f:
            f.write(json.dumps(line) + "\n")

    # ------------------------------------------------------------------
    # File upload. kie.ai's input fields take hosted URLs, so a local
    # reference goes through this first. The same file is reused across many
    # generations, so its URL is cached in Redis -- keyed by account (a hash
    # of the key) as well as path, since kie.ai namespaces uploads per
    # account -- and every hit is
    # confirmed live with a HEAD before it is trusted. Pure optimisation: a
    # broker outage just means the file is uploaded again.
    # ------------------------------------------------------------------
    def _upload_cache_key(self, path: str) -> str:
        """Per account (uploads are), without the key itself: the cache key
        names a Redis key and a lock, and is logged when the lock fails."""
        account = hashlib.sha256(self.api_key.encode()).hexdigest()[:16]
        return f"kieai:upload:{account}:{path}"

    def upload_local_file(self, path: str, upload_path: str = "refs") -> str:
        """One upload per file even when several requests start together
        (`upload_lock`): the rest wait and take the cached URL."""
        cache_key = self._upload_cache_key(path)
        with upload_lock(cache_key):
            return self._upload_once(cache_key, path, upload_path)

    def _upload_once(self, cache_key: str, path: str, upload_path: str) -> str:
        cached_url = get_text(cache_key)
        if cached_url is not None:
            if self._remote_file_exists(cached_url):
                logger.info("upload cache hit for %s -> %s", path, cached_url)
                return cached_url
            logger.info("cached url for %s no longer resolves, re-uploading", path)
            delete_text(cache_key)

        upload_file = self._fit_for_upload(pathlib.Path(path))
        logger.info("upload starting: %s", upload_file)
        with upload_file.open("rb") as fh:
            response = self._call(
                "POST",
                f"{self.UPLOAD_BASE}/api/file-stream-upload",
                files={"file": fh},
                data={"uploadPath": upload_path, "fileName": upload_file.name},
                timeout=settings.kieai.upload_timeout_s,
            )
        url = _parsed(response, KieUploadResponse).url()
        logger.info("upload done: %s -> %s", path, url)
        set_text(cache_key, url, settings.kieai.upload_cache_ttl_s)
        return url

    def _fit_for_upload(self, path: pathlib.Path) -> pathlib.Path:
        """`path`, or a shrunk JPEG copy of it when it is an image over
        kie.ai's reference limits -- an oversized reference is otherwise a 422
        from createTask ("file size exceeds limit") after the upload already
        succeeded. The original stays untouched (a character sheet is kept
        lossless), and the copy is reused on the next upload of the same file."""
        return fitted(path, self.OUT_DIR / "fitted", settings.kieai.ref_image_limits)

    def _remote_file_exists(self, url: str) -> bool:
        """Best-effort existence check for a previously-uploaded file's
        hosted URL. Fails closed: any error is treated as "not there", so the
        caller re-uploads rather than hand kie.ai a broken reference."""
        try:
            response = outbound.head(
                url,
                timeout=settings.kieai.remote_check_timeout_s,
                allow_redirects=True,
            )
        except requests.RequestException:
            return False
        return response.status_code < self._HTTP_ERROR

    # ------------------------------------------------------------------
    # The task queue every Market model shares.
    # ------------------------------------------------------------------
    def create_task(self, model: str, payload: Payload, job_id: str = "") -> str:
        response = self._call(
            "POST",
            f"{self.JOBS_BASE}/createTask",
            json={"model": model, "input": payload},
            timeout=settings.kieai.request_timeout_s,
        )
        created = _parsed(response, KieCreateResponse)
        created.raise_for_code()
        if created.data is None:
            msg = "kie.ai: createTask answered without a task id"
            raise ProviderUnavailableError(msg)
        task_id = created.data.task_id
        self._log_task(task_id, model, str(payload.get("prompt", "")), job_id)
        return task_id

    def get_task(self, task_id: str) -> KieRecord:
        """One recordInfo call, no waiting."""
        response = self._call(
            "GET",
            f"{self.JOBS_BASE}/recordInfo",
            params={"taskId": task_id},
            timeout=settings.kieai.request_timeout_s,
        )
        record = _parsed(response, KieRecordResponse)
        record.raise_for_code()
        if record.data is None:
            msg = f"kie.ai: no record for task {task_id}"
            raise ProviderUnavailableError(msg)
        logger.debug("kie.ai task %s: state=%s", task_id, record.data.state)
        if record.data.state == SUCCESS:
            self._log_completion(record.data)
        return record.data

    @classmethod
    def from_env(cls, api_key: str) -> "KieAIClient":
        s = settings.kieai
        return cls(
            api_key=api_key,
            out_dir=settings.aigen.out_dir,
            task_log=s.task_log,
            completions_log=s.completions_log,
        )
