import pathlib
from typing import Any, NoReturn
lazy from collections.abc import Mapping

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
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
from ofmhelpers.integrations.ai_providers.uploads import (
    memoized,
    remote_file_exists,
    upload_cache_key,
)
from ofmhelpers.log import get_logger
from ofmhelpers.media.image_fit import fitted
lazy from ofmhelpers.integrations.ai_providers.spec import Payload

logger = get_logger(__name__)


def _session() -> requests.Session:
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
    value = response.headers.get("Retry-After")
    try:
        return float(value) if value is not None else None
    except TypeError, ValueError:  # silent: not a number
        return None


def _refused(response: requests.Response) -> NoReturn:
    raise_for(response.status_code, response.text[:200], _retry_after_s(response))


class KieAIClient:
    def __init__(
        self,
        api_key: str,
        jobs_base: str | None = None,
        upload_base: str | None = None,
        out_dir: str | pathlib.Path = "./out",
    ) -> None:
        self.api_key = api_key
        self.JOBS_BASE = jobs_base or settings.kieai.jobs_base
        self.UPLOAD_BASE = upload_base or settings.kieai.upload_base
        self.HEADERS = {"Authorization": f"Bearer {self.api_key}"}

        self.OUT_DIR = pathlib.Path(out_dir)
        self.OUT_DIR.mkdir(parents=True, exist_ok=True)
        self._http = _session()

    def _call(
        self,
        method: str,
        url: str,
        timeout: float | None = None,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, Any] | None = None,
    ) -> requests.Response:
        return call(
            self._http,
            PROVIDER,
            _refused,
            method,
            url,
            headers=self.HEADERS,
            timeout=timeout,
            params=params,
            json=json,
            data=data,
            files=files,
        )

    def _log_task(self, task_id: str, model: str, prompt: str, job_id: str) -> None:
        logger.info(
            "kie.ai task created: taskId=%s model=%s jobId=%s prompt=%r",
            task_id,
            model,
            job_id,
            prompt,
        )

    def _log_completion(self, record: KieRecord) -> None:
        logger.info(
            "kie.ai task completed: taskId=%s credits=%s costTimeMs=%s",
            record.task_id,
            record.credits,
            record.cost_time_ms,
        )

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
        limits = settings.kieai.ref_image_limits.with_max_bytes(max_bytes)
        return fitted(path, self.OUT_DIR / "fitted", limits)

    def _remote_file_exists(self, url: str) -> bool:
        return remote_file_exists(url, settings.kieai.remote_check_timeout_s)

    def create_task(
        self, model: str, payload: Payload, job_id: str = "", callback_url: str = ""
    ) -> str:
        body: dict[str, Any] = {"model": model, "input": payload}
        if callback_url:
            body["callBackUrl"] = callback_url
        response = self._call(
            "POST",
            f"{self.JOBS_BASE}/createTask",
            json=body,
            timeout=settings.kieai.create_timeout_s,
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
    def from_env(cls, api_key: str) -> KieAIClient:
        return cls(api_key=api_key, out_dir=settings.aigen.out_dir)
