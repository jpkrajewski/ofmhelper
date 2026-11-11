"""RunningHub's Standard Model API: upload a reference file, submit a model
by its path, query a task. Single calls only; waiting, resuming and
downloading are `runner.py`'s.

RunningHub has no idempotency key: a submit is sent once and never retried
here (a lost answer would start, and charge, a second task). Through
`integrations.outbound`, so OFM_FAKE_EXTERNALS answers it from `fake.py`.
"""

from pathlib import Path
from typing import Any, NoReturn

lazy import requests

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.integrations.ai_providers.http import bearer_session, call, parsed
from ofmhelpers.integrations.ai_providers.runninghub.models import (
    PROVIDER,
    RhTask,
    RhUpload,
    raise_for,
)
from ofmhelpers.integrations.ai_providers.uploads import memoized, upload_cache_key
from ofmhelpers.log import get_logger
from ofmhelpers.media.image_fit import fitted

logger = get_logger(__name__)

API = "/openapi/v2"


def _refused(response: requests.Response) -> NoReturn:
    raise_for(response.status_code, response.text[:200])


class RunningHubClient:
    """One RunningHub account (its API key)."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._base = settings.runninghub.base_url.rstrip("/") + API
        self._http = bearer_session(api_key)

    def _post(self, path: str, **kwargs: Any) -> requests.Response:
        return call(
            self._http,
            PROVIDER,
            _refused,
            "POST",
            f"{self._base}/{path}",
            timeout=settings.runninghub.request_timeout_s,
            **kwargs,
        )

    @staticmethod
    def _task(response: requests.Response) -> RhTask:
        task = parsed(response, RhTask, PROVIDER)
        task.raise_for_error()
        return task

    # --- upload -----------------------------------------------------------
    # The URL is signed for 24 h, so it is kept (per account, per file) for
    # less than that: the same subject image across a batch uploads once.

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        return memoized(
            upload_cache_key("runninghub", self.api_key, path, max_bytes),
            settings.runninghub.upload_cache_ttl_s,
            lambda: self._upload(path, max_bytes),
        )

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
        url = parsed(response, RhUpload, PROVIDER, "upload answer").url()
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
