"""
Single import point for app config: `from ofmhelpers.config import settings`.

`settings` is a lazy container -- each group property (settings.web,
settings.kieai, ...) answers that group's BaseSettings as the environment is
*now*: several tests call monkeypatch.setenv()/delenv() inside a test
function body and expect the very next call into application code to
observe the new value (see tests/test_auth.py, test_recovery.py). A frozen
`settings = Settings()` singleton would ignore every later monkeypatch.

A group is built once per state of its inputs, though, not once per access:
the process environment, the group's `env_file` (tests blank it) and that
file's mtime (looked at no more than every `_DOTENV_CHECK_S`). Building one
reads and parses `.env` and validates every field -- /reels built groups
310 times per request, ~0.4 s of a 0.5 s page (prod profile, 2026-10-22).

Call-site convention (mirrors how each var was read before this module
existed):
  - construct-once-at-import: assign `settings.<group>.<field>` to a
    module-level constant, same as the old `X = os.getenv(...)` pattern.
  - fresh-per-call: reference `settings.<group>.<field>` inline inside the
    function that needs it, every time -- never stash it in a module-level
    or long-lived variable.
"""

import os
import time
from pathlib import Path
from typing import TypeVar

from pydantic_settings import BaseSettings

from ofmhelpers.config.settings import (
    AccountsSettings,
    AigenSettings,
    DownloadersSettings,
    ExportSettings,
    GDriveSettings,
    HiggsfieldSettings,
    InfraSettings,
    InstagramSettings,
    KieAISettings,
    LoggingSettings,
    SessionSettings,
    SignInSettings,
    TelemetrySettings,
    VaultSettings,
    WebSettings,
)

_Group = TypeVar("_Group", bound=BaseSettings)

# How stale a `.env` edit may go unseen: a stat per access is slow on a
# bind mount (dev on Windows).
_DOTENV_CHECK_S = 1.0


class _Dotenv:
    """Env files' mtimes, each re-read at most every `_DOTENV_CHECK_S`."""

    def __init__(self) -> None:
        self._seen: dict[str, tuple[float, int | None]] = {}

    def mtime(self, path: str) -> int | None:
        now = time.monotonic()
        checked = self._seen.get(path)
        if checked is not None and now - checked[0] < _DOTENV_CHECK_S:
            return checked[1]
        try:
            mtime: int | None = Path(path).stat().st_mtime_ns
        except OSError:
            mtime = None
        self._seen[path] = (now, mtime)
        return mtime


class Settings:
    def __init__(self) -> None:
        self._built: dict[type[BaseSettings], tuple[tuple, BaseSettings]] = {}
        self._dotenv = _Dotenv()

    def _inputs(self, cls: type[BaseSettings]) -> tuple:
        env_file = cls.model_config.get("env_file")
        mtime = self._dotenv.mtime(str(env_file)) if env_file else None
        return (hash(frozenset(os.environ.items())), env_file, mtime)

    def _group(self, cls: type[_Group]) -> _Group:
        """`cls` as the environment is now; built again only when that
        changed."""
        inputs = self._inputs(cls)
        built = self._built.get(cls)
        if built is not None and built[0] == inputs:
            return built[1]  # type: ignore[return-value]
        made = cls()
        self._built[cls] = (inputs, made)
        return made

    @property
    def session(self) -> SessionSettings:
        return self._group(SessionSettings)

    @property
    def web(self) -> WebSettings:
        return self._group(WebSettings)

    @property
    def infra(self) -> InfraSettings:
        return self._group(InfraSettings)

    @property
    def kieai(self) -> KieAISettings:
        return self._group(KieAISettings)

    @property
    def aigen(self) -> AigenSettings:
        return self._group(AigenSettings)

    @property
    def higgsfield(self) -> HiggsfieldSettings:
        return self._group(HiggsfieldSettings)

    @property
    def downloaders(self) -> DownloadersSettings:
        return self._group(DownloadersSettings)

    @property
    def instagram(self) -> InstagramSettings:
        return self._group(InstagramSettings)

    @property
    def accounts(self) -> AccountsSettings:
        return self._group(AccountsSettings)

    @property
    def exports(self) -> ExportSettings:
        return self._group(ExportSettings)

    @property
    def signin(self) -> SignInSettings:
        return self._group(SignInSettings)

    @property
    def telemetry(self) -> TelemetrySettings:
        return self._group(TelemetrySettings)

    @property
    def vault(self) -> VaultSettings:
        return self._group(VaultSettings)

    @property
    def gdrive(self) -> GDriveSettings:
        return self._group(GDriveSettings)

    @property
    def logging(self) -> LoggingSettings:
        return self._group(LoggingSettings)


settings = Settings()

__all__ = ["Settings", "settings"]
