import importlib
import inspect
import pkgutil
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from functools import lru_cache, partial
from pathlib import Path
from typing import ClassVar, Literal

import requests
from pydantic import BaseModel
from redis.exceptions import LockError, RedisError

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.generation import KeySlot
from ofmhelpers.utils.registry import Registry
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

logger = get_logger(__name__)


class TaskState(BaseModel):
    state: Literal["pending", "done", "failed"]
    urls: list[str] = []
    error: str | None = None
    no_credits: bool = False
    unfetched: bool = False
    cost_usd: float | None = None


class Provider(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    needs_key: ClassVar[bool] = True
    rank: ClassVar[int] = 100
    idempotent_submit: ClassVar[bool] = False

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def min_poll_interval_s(self) -> float:
        return 0

    @classmethod
    def notice(cls) -> str:
        return ""

    def send_lighter(self) -> bool:
        return False

    def callback_url(self, job_key: str) -> str:  # noqa: ARG002
        return ""

    @classmethod
    def callback_task_id(
        cls,
        raw: bytes,  # noqa: ARG003
        headers: Mapping[str, str],  # noqa: ARG003
    ) -> str | None:
        return None

    @classmethod
    def callback_signed(cls) -> bool:
        return False

    @classmethod
    def catalog_path(cls) -> Path:
        return Path(inspect.getfile(cls)).with_name("catalog.json")

    @classmethod
    @abstractmethod
    def default_key(cls) -> str | None: ...

    @classmethod
    def env_keys(cls) -> list[str]:
        key = cls.default_key()
        return [key] if key else []

    @classmethod
    def key_slots(cls) -> list[KeySlot]:
        return [KeySlot(id=cls.name, label=cls.label)]

    @classmethod
    def key_slot(cls, model_id: str) -> str:  # noqa: ARG003
        return cls.name

    @classmethod
    def slot_of_key(cls, api_key: str) -> str | None:  # noqa: ARG003
        return None

    @abstractmethod
    def upload(self, path: Path, max_bytes: int | None = None) -> str: ...

    @abstractmethod
    def submit(
        self, spec: ModelSpec, payload: Payload, idempotency_key: str
    ) -> str: ...

    @abstractmethod
    def status(self, task_id: str) -> TaskState: ...

    def download(self, url: str, dest: Path) -> Path:
        try:
            with outbound.get(
                url, stream=True, timeout=settings.aigen.download_timeout_s
            ) as response:
                response.raise_for_status()
                with dest.open("wb") as f:
                    f.writelines(response.iter_content(1 << 16))
        except requests.RequestException as exc:
            dest.unlink(missing_ok=True)
            msg = f"{self.label}: could not download {url}"
            raise ProviderUnavailableError(msg) from exc
        return dest


@lru_cache(maxsize=1)
def _registry() -> dict[str, type[Provider]]:
    return {}


def register_provider[P: type[Provider]](cls: P) -> P:
    _registry()[cls.name] = cls
    return cls


@lru_cache(maxsize=1)
def _import_providers() -> None:
    package = importlib.import_module("ofmhelpers.integrations.ai_providers")
    for module in pkgutil.iter_modules(package.__path__):
        if module.ispkg:
            try:
                importlib.import_module(f"{package.__name__}.{module.name}.provider")
            except ModuleNotFoundError as exc:
                if not (exc.name or "").endswith(".provider"):
                    raise


def provider_classes() -> Registry[type[Provider]]:
    _import_providers()
    ordered = sorted(_registry().values(), key=lambda c: (c.rank, c.name))
    return Registry[type[Provider]](entries={cls.name: cls for cls in ordered})


def provider_names() -> list[str]:
    return provider_classes().names()


def provider_class(name: str) -> type[Provider]:
    try:
        return provider_classes()[name]
    except KeyError:
        msg = f"Unknown AI provider: {name}"
        raise KeyError(msg) from None


def get_provider(name: str, api_key: str) -> Provider:
    return provider_class(name)(api_key)


def uploader(provider: Provider, spec: ModelSpec) -> Callable[[Path], str]:
    if spec.max_upload_bytes is None:
        return provider.upload
    return partial(provider.upload, max_bytes=spec.max_upload_bytes)


@contextmanager
def upload_lock(cache_key: str) -> Iterator[None]:
    wait_s = settings.aigen.upload_lock_s
    lock = None
    try:
        lock = get_redis().lock(
            f"lock:{cache_key}", timeout=wait_s, blocking_timeout=wait_s
        )
        held = bool(lock.acquire())
    except RedisError:
        logger.warning("upload lock unavailable for %s", cache_key, exc_info=True)
        held = False
    try:
        yield
    finally:
        if held and lock is not None:
            try:
                lock.release()
            except RedisError, LockError:
                logger.warning(
                    "upload lock for %s not released", cache_key, exc_info=True
                )
