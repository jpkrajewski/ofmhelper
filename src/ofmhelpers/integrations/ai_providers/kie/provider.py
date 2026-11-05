"""kie.ai behind the shared `Provider` interface."""

from collections.abc import Mapping
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers import self_heal
from ofmhelpers.integrations.ai_providers.base import (
    Provider,
    TaskState,
    register_provider,
)
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.integrations.ai_providers.kie import callback
from ofmhelpers.integrations.ai_providers.kie.client import KieAIClient
from ofmhelpers.integrations.ai_providers.kie.models import FAIL, SUCCESS, KieRecord
from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload


@register_provider
class KieProvider(Provider):
    name = "kie"
    label = "kie.ai"
    rank = 0

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        self.client = KieAIClient.from_env(api_key)

    @classmethod
    def default_key(cls) -> str | None:
        return settings.web.kie_ai_api_key_admin

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        return self.client.upload_local_file(str(path), max_bytes=max_bytes)

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        """kie.ai has no idempotency key; a re-run never resubmits anyway
        (the runner resumes the remembered task), and createTask is never
        retried by the client. Its callback names a task whose answer was
        lost."""
        return self.client.create_task(
            spec.model_id,
            payload,
            job_id=idempotency_key,
            callback_url=self.callback_url(idempotency_key),
        )

    def callback_url(self, job_key: str) -> str:
        return self_heal.callback_url(self.name, job_key)

    @classmethod
    def callback_task_id(cls, raw: bytes, headers: Mapping[str, str]) -> str | None:
        return callback.task_id(raw, headers)

    @classmethod
    def callback_signed(cls) -> bool:
        return settings.kieai.webhook_hmac_key is not None

    def status(self, task_id: str) -> TaskState:
        record = self.client.get_task(task_id)
        if record.state == SUCCESS:
            return TaskState(
                state="done",
                urls=self._result_urls(record),
                cost_usd=(
                    record.credits * settings.web.kie_usd_per_credit
                    if record.credits is not None
                    else None
                ),
            )
        if record.state == FAIL:
            return TaskState(state="failed", error=record.fail_msg)
        return TaskState(state="pending")

    @staticmethod
    def _result_urls(record: KieRecord) -> list[str]:
        """A finished (paid) task whose `resultJson` cannot be read is asked
        again, not failed: the job stays resumable instead of forgetting it."""
        try:
            return record.result_urls
        except (ValueError, AttributeError, TypeError) as exc:
            msg = f"kie.ai: unreadable result of task {record.task_id}"
            raise ProviderUnavailableError(msg) from exc
