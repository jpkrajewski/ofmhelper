# Copyright (C) 2026 jpkrajewski

lazy from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.base import (
    Provider,
    TaskState,
    register_provider,
)
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.integrations.ai_providers.runninghub.client import RunningHubClient
from ofmhelpers.integrations.ai_providers.runninghub.models import (
    FAILED,
    PENDING,
    SUCCESS,
    is_no_credits,
)
from ofmhelpers.integrations.ai_providers.spec import Fields
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

WIRE_TYPE = "x-wire"


@register_provider
class RunningHubProvider(Provider):
    name = "runninghub"
    label = "RunningHub"
    rank = 20

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        self.client = RunningHubClient(api_key)

    @classmethod
    def default_key(cls) -> str | None:
        return settings.runninghub.api_key_admin

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        return self.client.upload(path, max_bytes)

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        return self.client.submit(
            spec.model_id,
            wire_body(spec, payload).model_dump(),
            job=idempotency_key,
        )

    def status(self, task_id: str) -> TaskState:
        task = self.client.query(task_id)
        if task.status == SUCCESS:
            if not task.urls:
                msg = f"RunningHub: task {task_id} succeeded without a result"
                raise ProviderUnavailableError(msg)
            return TaskState(state="done", urls=task.urls)
        if task.status in PENDING:
            return TaskState(state="pending")
        if task.status in FAILED:
            return TaskState(
                state="failed",
                error=task.failure,
                no_credits=is_no_credits(task.code),
            )
        msg = f"RunningHub: task {task_id} answered status {task.status!r}"
        raise ProviderUnavailableError(msg)


def wire_body(spec: ModelSpec, payload: Payload) -> Fields:
    raw = spec.input_schema.get("properties")
    schema = raw if isinstance(raw, dict) else {}
    body = dict(payload)
    for name, field in schema.items():
        if not isinstance(field, dict):
            continue
        if name not in body and "default" in field:
            body[name] = field["default"]
        if field.get(WIRE_TYPE) == "string" and name in body:
            body[name] = str(body[name])
    return Fields(values=body)
