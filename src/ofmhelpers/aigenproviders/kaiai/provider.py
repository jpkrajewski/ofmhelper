"""kie.ai behind the shared `Provider` interface."""

from pathlib import Path

from ofmhelpers.aigenproviders.base import Provider, TaskState, register_provider
from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.models import FAIL, SUCCESS
from ofmhelpers.aigenproviders.spec import ModelSpec, Payload
from ofmhelpers.config import settings


@register_provider
class KieProvider(Provider):
    name = "kie"
    label = "kie.ai"
    rank = 0

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        self.client = KieAIClient.from_env(api_key)

    @classmethod
    def default_key(cls, admin: bool) -> str | None:
        web = settings.web
        return web.kie_ai_api_key_admin if admin else web.kie_ai_api_key_va

    def upload(self, path: Path) -> str:
        return self.client.upload_local_file(str(path))

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        """kie.ai has no idempotency key; a re-run never resubmits anyway
        (the runner resumes the remembered task), and createTask is never
        retried by the client."""
        return self.client.create_task(spec.model_id, payload, job_id=idempotency_key)

    def status(self, task_id: str) -> TaskState:
        record = self.client.get_task(task_id)
        if record.state == SUCCESS:
            return TaskState(state="done", urls=record.result_urls)
        if record.state == FAIL:
            return TaskState(state="failed", error=record.fail_msg)
        return TaskState(state="pending")
