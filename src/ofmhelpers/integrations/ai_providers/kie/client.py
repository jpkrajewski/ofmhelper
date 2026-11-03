"""The kie.ai Market API: upload a reference file, create a task, read it.

Single calls only. Which model, with what input, comes from the catalog
(`catalog.json`); waiting, resuming and downloading are `runner.py`'s. Every
response is parsed into `models.py`, which also maps kie.ai's in-body error
codes onto the shared error types.
"""

import json
import pathlib
import time
from typing import Any, NoReturn

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.integrations.ai_providers.http import call, parsed
from ofmhelpers.integrations.ai_providers.kie.models import (
    PROVIDER,
    SUCCESS,
    KieCreateResponse,
    KieRecord,
    KieRecordResponse,
    KieUploadResponse,
    raise_for,
)
from ofmhelpers.integrations.ai_providers.spec import Payload
from ofmhelpers.integrations.ai_providers.uploads import (
    memoized,
    remote_file_exists,
    upload_cache_key,
)
from ofmhelpers.log import get_logger
from ofmhelpers.media.image_fit import fitted
from ofmhelpers.utils import outbound

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


def _refused(response: requests.Response) -> NoReturn:
    """Normally kie.ai answers 200 with its code in the body; a real HTTP error
    is mapped by its status the same way."""
    raise_for(response.status_code, response.text[:200], _retry_after_s(response))


class KieAIClient:
    """One kie.ai account. Paths and URLs are injected so tests and several
    keys can coexist in one process."""

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
        return call(
            self._http, PROVIDER, _refused, method, url, headers=self.HEADERS, **kwargs
        )

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
        self._append(self.TASK_LOG, record)

    def _log_completion(self, record: KieRecord) -> None:
        line = {
            "taskId": record.task_id,
            "credits": record.credits,
            "costTimeMs": record.cost_time_ms,
        }
        self._append(self.COMPLETIONS_LOG, line)

    @staticmethod
    def _append(path: pathlib.Path, line: dict[str, Any]) -> None:
        """One line of an audit log. Never raises: it runs right after the
        request is paid for, and a full disk must not lose the task id."""
        try:
            with path.open("a") as f:
                f.write(json.dumps(line) + "\n")
        except OSError:
            logger.warning("could not write %s", path, exc_info=True)

    # ------------------------------------------------------------------
    # File upload. kie.ai's input fields take hosted URLs, so a local
    # reference goes through this first. The same file is reused across many
    # generations, so its URL is memoized (`uploads.memoized`), and every hit
    # is confirmed live with a HEAD before it is trusted.
    # ------------------------------------------------------------------
    def _upload_cache_key(self, path: str, max_bytes: int | None = None) -> str:
        return upload_cache_key("kieai", self.api_key, path, max_bytes)

    def upload_local_file(
        self, path: str, upload_path: str = "refs", max_bytes: int | None = None
    ) -> str:
        return memoized(
            self._upload_cache_key(path, max_bytes),
            settings.kieai.upload_cache_ttl_s,
            lambda: self._upload(path, upload_path, max_bytes),
            live=self._remote_file_exists,
        )

    def _upload(self, path: str, upload_path: str, max_bytes: int | None) -> str:
        upload_file = self._fit_for_upload(pathlib.Path(path), max_bytes)
        logger.info("upload starting: %s", upload_file)
        with upload_file.open("rb") as fh:
            response = self._call(
                "POST",
                f"{self.UPLOAD_BASE}/api/file-stream-upload",
                files={"file": fh},
                data={"uploadPath": upload_path, "fileName": upload_file.name},
                timeout=settings.kieai.upload_timeout_s,
            )
        url = parsed(response, KieUploadResponse, PROVIDER).url()
        logger.info("upload done: %s -> %s", path, url)
        return url

    def _fit_for_upload(
        self, path: pathlib.Path, max_bytes: int | None = None
    ) -> pathlib.Path:
        """`path`, or a shrunk JPEG copy of it when it is an image over
        kie.ai's reference limits -- an oversized reference is otherwise a 422
        from createTask ("file size exceeds limit") after the upload already
        succeeded. The original stays untouched (a character sheet is kept
        lossless), and the copy is reused on the next upload of the same file."""
        limits = settings.kieai.ref_image_limits.with_max_bytes(max_bytes)
        return fitted(path, self.OUT_DIR / "fitted", limits)

    def _remote_file_exists(self, url: str) -> bool:
        return remote_file_exists(url, settings.kieai.remote_check_timeout_s)

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
        created = parsed(response, KieCreateResponse, PROVIDER)
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
        record = parsed(response, KieRecordResponse, PROVIDER)
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
