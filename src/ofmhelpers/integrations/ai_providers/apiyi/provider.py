"""APIYI (api.apiyi.com) behind the shared `Provider` interface: one gateway in
front of several APIs, each a family in `families/` (Wan over DashScope,
Seedance, Veo, Nano Banana over Gemini, GPT-Image / Seedream / FLUX over
OpenAI images). This class only dispatches: a model's family by its id, a
task's by the tag in its id; the key by the model's token group
(`keys.key_for`).

Self-heal (`self_heal.py`): no APIYI API has an idempotency key, a callback
or a task list, so a submit whose answer is lost is never sent again (never
paid twice), as on RunningHub."""

from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.apiyi.client import ApiyiClient
from ofmhelpers.integrations.ai_providers.apiyi.families import (
    families,
    family_for,
    split,
)
from ofmhelpers.integrations.ai_providers.apiyi.keys import key_for
from ofmhelpers.integrations.ai_providers.apiyi.models import PROVIDER
from ofmhelpers.integrations.ai_providers.base import (
    Provider,
    TaskState,
    register_provider,
)
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload


@register_provider
class ApiyiProvider(Provider):
    name = "apiyi"
    label = PROVIDER
    rank = 40

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        self.client = ApiyiClient(api_key)

    @classmethod
    def default_key(cls) -> str | None:
        return settings.apiyi.api_key_admin

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        return self.client.upload(path, max_bytes)

    def min_poll_interval_s(self) -> float:
        return settings.apiyi.min_poll_interval_s

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        """No idempotency key on APIYI (ours only names the job in the log); a
        re-run resumes the remembered task, nothing resends a submit. An
        admin key is swapped for the key of the model's group first; the
        runner remembers the key the task was made with."""
        self._use_key(key_for(spec.model_id, self.api_key))
        family = family_for(spec.model_id)
        own = family.submit(self.client, spec, payload, idempotency_key)
        return family.task_id(own)

    def _use_key(self, key: str) -> None:
        if key != self.api_key:
            self.api_key = key
            self.client = ApiyiClient(key)

    def status(self, task_id: str) -> TaskState:
        task = split(task_id)
        return task.family.status(self.client, task.own_id)

    def download(self, url: str, dest: Path) -> Path:
        for family in families():
            saved = family.download(self.client, url, dest)
            if saved is not None:
                return saved
        return super().download(url, dest)
