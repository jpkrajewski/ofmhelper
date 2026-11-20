from datetime import UTC, datetime
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.apiyi.client import ApiyiClient
from ofmhelpers.integrations.ai_providers.apiyi.families import (
    families,
    family_for,
    split,
)
from ofmhelpers.integrations.ai_providers.apiyi.hours import slow_notice
from ofmhelpers.integrations.ai_providers.apiyi.keys import (
    SLOTS,
    admin_keys,
    key_for,
    slot_listing,
    slot_of,
)
from ofmhelpers.integrations.ai_providers.apiyi.models import PROVIDER
from ofmhelpers.integrations.ai_providers.base import (
    Provider,
    TaskState,
    register_provider,
)
from ofmhelpers.integrations.ai_providers.errors import ProviderError
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload
lazy from ofmhelpers.schemas.generation import KeySlot

logger = get_logger(__name__)


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

    @classmethod
    def env_keys(cls) -> list[str]:
        return admin_keys().all()

    @classmethod
    def key_slots(cls) -> list[KeySlot]:
        return list(SLOTS)

    @classmethod
    def key_slot(cls, model_id: str) -> str:
        return slot_of(model_id)

    @classmethod
    def slot_of_key(cls, api_key: str) -> str | None:
        try:
            return slot_listing(ApiyiClient(api_key).model_ids())
        except ProviderError:
            logger.warning("apiyi: a key's models could not be listed", exc_info=True)
            return None

    @classmethod
    def notice(cls) -> str:
        return slow_notice(datetime.now(UTC))

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        return self.client.upload(path, max_bytes)

    def min_poll_interval_s(self) -> float:
        return settings.apiyi.min_poll_interval_s

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        self._use_key(key_for(spec.model_id, self.api_key))
        family = family_for(spec.model_id)
        own = family.submit(self.client, spec, payload, idempotency_key)
        return family.task_id(own)

    def _use_key(self, key: str) -> None:
        if key != self.api_key:
            self.api_key = key
            self.client = ApiyiClient(key, lighter=self.client.lighter)

    def send_lighter(self) -> bool:
        self.client.lighter = True
        return True

    def status(self, task_id: str) -> TaskState:
        task = split(task_id)
        return task.family.status(self.client, task.own_id)

    def download(self, url: str, dest: Path) -> Path:
        for family in families():
            saved = family.download(self.client, url, dest)
            if saved is not None:
                return saved
        return super().download(url, dest)
