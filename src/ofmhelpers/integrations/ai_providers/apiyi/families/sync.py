import base64
import secrets
from abc import abstractmethod
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.apiyi.client import (
    LOCAL,
    ApiyiClient,
    local_path,
)
from ofmhelpers.integrations.ai_providers.apiyi.families import Family
from ofmhelpers.integrations.ai_providers.apiyi.models import PROVIDER
from ofmhelpers.integrations.ai_providers.base import TaskState
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

logger = get_logger(__name__)

_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class Made(BaseModel):
    urls: list[str] = []
    images: list[tuple[str, str]] = []


class Recorded(BaseModel):
    results: list[str]


def _results_dir() -> Path:
    return Path(settings.aigen.out_dir) / "apiyi" / "results"


def _strip_data_url(data: str) -> str:
    return data.split(",", 1)[1] if data.startswith("data:") else data


class SyncImages(Family):
    tag = "img"

    @abstractmethod
    def make(self, client: ApiyiClient, spec: ModelSpec, payload: Payload) -> Made: ...

    def submit(
        self, client: ApiyiClient, spec: ModelSpec, payload: Payload, job: str
    ) -> str:
        made = self.make(client, spec, payload)
        own = secrets.token_hex(12)
        folder = _results_dir()
        folder.mkdir(parents=True, exist_ok=True)
        results = list(made.urls)
        for n, (mime, data) in enumerate(made.images):
            path = folder / f"{own}-{n}{_SUFFIX.get(mime, '.png')}"
            path.write_bytes(base64.b64decode(_strip_data_url(data)))
            results.append(f"{LOCAL}{path}")
        if not results:
            msg = f"{PROVIDER}: {spec.model_id} answered without an image"
            raise ProviderUnavailableError(msg, maybe_made=False)
        (folder / f"{own}.json").write_text(Recorded(results=results).model_dump_json())
        logger.info(
            "apiyi %s made %s image(s) as %s (job %s)",
            spec.model_id,
            len(results),
            own,
            job,
        )
        return own

    def status(self, client: ApiyiClient, task_id: str) -> TaskState:  # noqa: ARG002
        record = _results_dir() / f"{task_id}.json"
        if not record.is_file():
            return TaskState(
                state="failed", error=f"{PROVIDER}: result {task_id} is gone"
            )
        results = Recorded.model_validate_json(record.read_text()).results
        return TaskState(state="done", urls=results)

    def download(self, client: ApiyiClient, url: str, dest: Path) -> Path | None:  # noqa: ARG002
        path = local_path(url)
        if path is None:
            return None
        if not path.is_file():
            msg = f"{PROVIDER}: {path.name} is gone from this server"
            raise ProviderUnavailableError(msg)
        dest.write_bytes(path.read_bytes())
        return dest
