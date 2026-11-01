"""RunningHub's Standard Model API: upload a reference file, submit a model
by its path, query a task. Single calls only; waiting, resuming and
downloading are `runner.py`'s.

RunningHub has no idempotency key: a submit is sent once and never retried
here (a lost answer would start, and charge, a second task). Through
`utils.outbound`, so OFM_FAKE_EXTERNALS answers it from `fake.py`.
"""

import hashlib
from pathlib import Path
from typing import Any

import requests

from ofmhelpers.aigenproviders.base import upload_lock
from ofmhelpers.aigenproviders.errors import ProviderUnavailableError
from ofmhelpers.aigenproviders.runninghub.models import (
    RhTask,
    RhUpload,
    raise_for,
)
from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound
from ofmhelpers.utils.image_fit import fitted

logger = get_logger(__name__)

API = "/openapi/v2"
_HTTP_ERROR = 400


class RunningHubClient:
    """One RunningHub account (its API key)."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._base = settings.runninghub.base_url.rstrip("/") + API
        self._http = outbound.mount(requests.Session())
        self._http.headers["Authorization"] = f"Bearer {api_key}"

    def _post(self, path: str, **kwargs: Any) -> requests.Response:
        """One POST; a transport failure is `ProviderUnavailableError`, an
        HTTP error the error of its status."""
        try:
            response = self._http.post(
                f"{self._base}/{path}",
                timeout=settings.runninghub.request_timeout_s,
                **kwargs,
            )
        except requests.RequestException as exc:
            msg = f"RunningHub unreachable: {exc.__class__.__name__}"
            raise ProviderUnavailableError(msg) from exc
        if response.status_code >= _HTTP_ERROR:
            raise_for(response.status_code, response.text[:200])
        return response

    @staticmethod
    def _task(response: requests.Response) -> RhTask:
        """A 200 that is not a task is RunningHub misbehaving: unavailable,
        so a poll keeps trying and a paid task stays remembered."""
        try:
            task = RhTask.model_validate(response.json())
        except ValueError as exc:  # JSONDecodeError and ValidationError alike
            msg = f"RunningHub: unreadable answer ({exc.__class__.__name__})"
            raise ProviderUnavailableError(msg) from exc
        task.raise_for_error()
        return task

    # --- upload -----------------------------------------------------------
    # The URL is signed for 24 h, so it is kept (per account, per file) for
    # less than that: the same subject image across a batch uploads once.

    def _cache_key(self, path: Path) -> str:
        account = hashlib.sha256(self.api_key.encode()).hexdigest()[:16]
        return f"runninghub:upload:{account}:{path}"

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        cache_key = self._cache_key(path)
        if max_bytes:
            cache_key = f"{cache_key}:{max_bytes}"
        with upload_lock(cache_key):
            cached = get_text(cache_key)
            if cached is not None:
                return cached
            url = self._upload(path, max_bytes)
            set_text(cache_key, url, settings.runninghub.upload_cache_ttl_s)
            return url

    def _upload(self, path: Path, max_bytes: int | None) -> str:
        sent = fitted(
            path,
            Path(settings.aigen.out_dir) / "fitted",
            settings.runninghub.ref_image_limits.with_max_bytes(max_bytes),
        )
        with sent.open("rb") as fh:
            response = self._post(
                "media/upload/binary", files={"file": (sent.name, fh)}
            )
        try:
            url = RhUpload.model_validate(response.json()).url()
        except ValueError as exc:
            msg = f"RunningHub: unreadable upload answer ({exc.__class__.__name__})"
            raise ProviderUnavailableError(msg) from exc
        logger.info("runninghub upload %s -> %s", path.name, url.split("?")[0])
        return url

    # --- tasks ------------------------------------------------------------

    def submit(self, endpoint: str, body: dict[str, Any], job: str) -> str:
        """Starts the model at `endpoint` ("alibaba/wan-3.0/reference-to-video")
        for our `job`; its task id."""
        task = self._task(self._post(endpoint, json=body))
        if not task.task_id:
            msg = f"RunningHub: {endpoint} answered without a task id"
            raise ProviderUnavailableError(msg)
        logger.info(
            "runninghub %s task %s %s (job %s)",
            endpoint,
            task.task_id,
            task.status,
            job,
        )
        return task.task_id

    def query(self, task_id: str) -> RhTask:
        task = self._task(self._post("query", json={"taskId": task_id}))
        if task.usage is not None and task.status not in ("", "QUEUED", "RUNNING"):
            logger.info(
                "runninghub task %s %s: %s money, %s coins, %s s",
                task_id,
                task.status,
                task.usage.consume_money,
                task.usage.consume_coins,
                task.usage.cost_time_s,
            )
        return task
