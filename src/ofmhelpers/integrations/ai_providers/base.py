"""The one interface every AI provider implements, and how providers are found.

A provider is a subpackage of `integrations/ai_providers` with a `provider.py` defining
one `Provider` subclass decorated `@register_provider`, and a `catalog.json`
of its models next to it. The form, the generic router and recovery go through
`provider_class` / `provider_names` and never name one; what a new provider still
touches outside its subpackage is the checklist in this package's CLAUDE.md.

Three calls make a generation -- `upload` a reference file, `submit` the
request, `status` it until it ends -- and `download` fetches a result. The
lifecycle around them (polling, resuming after a crash, saving results) is
written once, in `runner.py`.
"""

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
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound

logger = get_logger(__name__)


class TaskState(BaseModel):
    """Where a submitted request stands. `urls` is set once it is `done`,
    `error` once it `failed` (moderation included); `no_credits`: it failed
    for want of credits (nothing made, nothing paid); `cost_usd`: what it cost,
    when the provider says."""

    state: Literal["pending", "done", "failed"]
    urls: list[str] = []
    error: str | None = None
    no_credits: bool = False
    cost_usd: float | None = None


class Provider(ABC):
    """One account on one provider: built per request with the key it runs
    on, since keys are per role or pasted into the form."""

    name: ClassVar[str]
    label: ClassVar[str]
    # False for a provider that runs without an account (the fake one).
    needs_key: ClassVar[bool] = True
    # Where it sits in the provider select, lowest first.
    rank: ClassVar[int] = 100
    # A submit resent with the same idempotency key answers the request
    # already made: one whose answer was lost is sent again, never paid twice.
    idempotent_submit: ClassVar[bool] = False

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def callback_url(self, job_key: str) -> str:  # noqa: ARG002 -- none by default
        """Where the provider tells of the request it made for `job_key`, so a
        submit whose answer was lost is still found (runner, recovery); empty
        when it cannot, or this server has no public address."""
        return ""

    @classmethod
    def callback_task_id(
        cls,
        raw: bytes,  # noqa: ARG003 -- none by default
        headers: Mapping[str, str],  # noqa: ARG003
    ) -> str | None:
        """The task id a genuine callback names; None for anything else."""
        return None

    @classmethod
    def callback_signed(cls) -> bool:
        """Whether `callback_task_id` checks the provider's signature: then the
        task needs no check with the job's key before it is believed."""
        return False

    @classmethod
    def catalog_path(cls) -> Path:
        """Its models: `catalog.json` next to the module defining it."""
        return Path(inspect.getfile(cls)).with_name("catalog.json")

    @classmethod
    @abstractmethod
    def default_key(cls) -> str | None:
        """The key an admin's form pre-fills when they saved none of their
        own (.env), if configured. Users only ever get their own."""

    @abstractmethod
    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        """Host a local reference file; the URL a request can point at.
        `max_bytes`: the model's own per-file limit when it is above the
        provider's default cap (`ModelSpec.max_upload_bytes`)."""

    @abstractmethod
    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        """Start a generation; the provider's id for it."""

    @abstractmethod
    def status(self, task_id: str) -> TaskState:
        """One status check, no waiting."""

    def download(self, url: str, dest: Path) -> Path:
        """Save a result locally. Result URLs are public on every provider so
        far; one that differs overrides this."""
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
    """Every registered provider by name (a singleton accessor, not module
    state)."""
    return {}


def register_provider[P: type[Provider]](cls: P) -> P:
    """Class decorator: make `cls` a provider the app offers."""
    _registry()[cls.name] = cls
    return cls


@lru_cache(maxsize=1)
def _import_providers() -> None:
    """Import every subpackage's `provider` module once, which registers
    its provider."""
    package = importlib.import_module("ofmhelpers.integrations.ai_providers")
    for module in pkgutil.iter_modules(package.__path__):
        if module.ispkg:
            try:
                importlib.import_module(f"{package.__name__}.{module.name}.provider")
            except ModuleNotFoundError as exc:
                if not (exc.name or "").endswith(".provider"):
                    raise


def provider_classes() -> dict[str, type[Provider]]:
    """Every provider by name, in the order the provider select lists them."""
    _import_providers()
    ordered = sorted(_registry().values(), key=lambda c: (c.rank, c.name))
    return {cls.name: cls for cls in ordered}


def provider_names() -> list[str]:
    return list(provider_classes())


def provider_class(name: str) -> type[Provider]:
    try:
        return provider_classes()[name]
    except KeyError:
        msg = f"Unknown AI provider: {name}"
        raise KeyError(msg) from None


def get_provider(name: str, api_key: str) -> Provider:
    return provider_class(name)(api_key)


def uploader(provider: Provider, spec: ModelSpec) -> Callable[[Path], str]:
    """`provider.upload`, with `spec`'s own file limit when it has one: what
    `spec.request` hands each reference to."""
    if spec.max_upload_bytes is None:
        return provider.upload
    return partial(provider.upload, max_bytes=spec.max_upload_bytes)


@contextmanager
def upload_lock(cache_key: str) -> Iterator[None]:
    """One upload per file (its upload-cache key) across every worker
    process: requests that start together -- /clone-long's pieces, a
    /clone-links batch, all with the same subject image -- upload it once,
    the others wait here and take the cached URL. A Redis lock: the worker is
    a pool of processes, and the old in-process lock let two /clone-links
    runs upload one image twice (dev 2026-10-25). Redis down or the wait
    over `OFM_UPLOAD_LOCK_S`: no lock, the file is uploaded again at worst.
    `cache_key` is logged: it must never carry an API key (hash it)."""
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
            except (RedisError, LockError):
                # Expired mid-upload (longer than OFM_UPLOAD_LOCK_S) or
                # Redis lost it: the upload itself went through.
                logger.warning("upload lock for %s not released", cache_key)
