"""The one interface every AI provider implements, and how providers are found.

A provider is a subpackage of `aigenproviders` with a `provider.py` defining
one `Provider` subclass decorated `@register_provider`, and a `catalog.json`
of its models next to it. Nothing else in
the app names a provider: the form, the generic router, /clone and recovery
all go through `provider_class` / `provider_names`, so adding one (fal.ai,
Replicate, ...) is that subpackage and nothing more.

Three calls make a generation -- `upload` a reference file, `submit` the
request, `status` it until it ends -- and `download` fetches a result. The
lifecycle around them (polling, resuming after a crash, saving results) is
written once, in `runner.py`.
"""

import importlib
import inspect
import pkgutil
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal

import requests
from pydantic import BaseModel

from ofmhelpers.aigenproviders.errors import ProviderUnavailableError
from ofmhelpers.aigenproviders.spec import ModelSpec, Payload
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound

logger = get_logger(__name__)


class TaskState(BaseModel):
    """Where a submitted request stands. `urls` is set once it is `done`,
    `error` once it `failed` (moderation included)."""

    state: Literal["pending", "done", "failed"]
    urls: list[str] = []
    error: str | None = None


class Provider(ABC):
    """One account on one provider: built per request with the key it runs
    on, since keys are per role or pasted into the form."""

    name: ClassVar[str]
    label: ClassVar[str]
    # False for a provider that runs without an account (the fake one).
    needs_key: ClassVar[bool] = True
    # Where it sits in the provider select, lowest first.
    rank: ClassVar[int] = 100

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    @classmethod
    def catalog_path(cls) -> Path:
        """Its models: `catalog.json` next to the module defining it."""
        return Path(inspect.getfile(cls)).with_name("catalog.json")

    @classmethod
    @abstractmethod
    def default_key(cls, admin: bool) -> str | None:
        """The key the form pre-fills for an admin or a VA, if configured."""

    @abstractmethod
    def upload(self, path: Path) -> str:
        """Host a local reference file; the URL a request can point at."""

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
    package = importlib.import_module("ofmhelpers.aigenproviders")
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
