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

Call-site convention: reference `settings.<group>.<field>` inline inside the
function that needs it, every time -- never stash it in a module-level or
long-lived variable. A few modules still capture a value at import (e.g.
`routers/generation/clone.py` DEFAULT_PROMPT, `routers/task_helpers/uploads.py`
ASSETS_ROOT) because tests patch those module attributes; they are not a
pattern to copy.
"""

import os
import time
from pathlib import Path
from typing import TypeVar

from pydantic_settings import BaseSettings

from ofmhelpers.config.settings import (
    AccountsSettings,
    AigenSettings,
    ApiyiSettings,
    ChattingSettings,
    CloneAiCheckSettings,
    CloneLinksSettings,
    CloneLongSettings,
    DownloadersSettings,
    ElyumSettings,
    EventsSettings,
    ExportSettings,
    FanvueSettings,
    GDriveSettings,
    HiggsfieldSettings,
    InfraSettings,
    InstagramSettings,
    KieAISettings,
    LinksSettings,
    LLMSettings,
    LoggingSettings,
    PicturesSettings,
    ReportsSettings,
    RepurposeSettings,
    RunningHubSettings,
    SessionSettings,
    SignInSettings,
    StackedSettings,
    TelegramSettings,
    TelemetrySettings,
    TrialsSettings,
    VaultSettings,
    ViralSettings,
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
    def runninghub(self) -> RunningHubSettings:
        return self._group(RunningHubSettings)

    @property
    def elyum(self) -> ElyumSettings:
        return self._group(ElyumSettings)

    @property
    def apiyi(self) -> ApiyiSettings:
        return self._group(ApiyiSettings)

    @property
    def pictures(self) -> PicturesSettings:
        return self._group(PicturesSettings)

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
    def clone_long(self) -> CloneLongSettings:
        return self._group(CloneLongSettings)

    @property
    def clone_links(self) -> CloneLinksSettings:
        return self._group(CloneLinksSettings)

    @property
    def signin(self) -> SignInSettings:
        return self._group(SignInSettings)

    @property
    def telemetry(self) -> TelemetrySettings:
        return self._group(TelemetrySettings)

    @property
    def links(self) -> LinksSettings:
        return self._group(LinksSettings)

    @property
    def vault(self) -> VaultSettings:
        return self._group(VaultSettings)

    @property
    def gdrive(self) -> GDriveSettings:
        return self._group(GDriveSettings)

    @property
    def llm(self) -> LLMSettings:
        return self._group(LLMSettings)

    @property
    def clone_ai_check(self) -> CloneAiCheckSettings:
        return self._group(CloneAiCheckSettings)

    @property
    def repurpose(self) -> RepurposeSettings:
        return self._group(RepurposeSettings)

    @property
    def trials(self) -> TrialsSettings:
        return self._group(TrialsSettings)

    @property
    def viral(self) -> ViralSettings:
        return self._group(ViralSettings)

    @property
    def stacked(self) -> StackedSettings:
        return self._group(StackedSettings)

    @property
    def fanvue(self) -> FanvueSettings:
        return self._group(FanvueSettings)

    @property
    def chatting(self) -> ChattingSettings:
        return self._group(ChattingSettings)

    @property
    def logging(self) -> LoggingSettings:
        return self._group(LoggingSettings)

    @property
    def reports(self) -> ReportsSettings:
        return self._group(ReportsSettings)

    @property
    def events(self) -> EventsSettings:
        return self._group(EventsSettings)

    @property
    def telegram(self) -> TelegramSettings:
        return self._group(TelegramSettings)


settings = Settings()

__all__ = ["Settings", "settings"]
