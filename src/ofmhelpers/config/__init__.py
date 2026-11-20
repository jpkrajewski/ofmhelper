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
    DistributionSettings,
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
    PinterestSettings,
    ReportsSettings,
    RepurposeSettings,
    RunningHubSettings,
    SentrySettings,
    ServerSettings,
    SessionSettings,
    SignInSettings,
    StackedSettings,
    TelegramSettings,
    TelemetrySettings,
    ThreadsSettings,
    TrialsSettings,
    VaultSettings,
    ViralSettings,
    WebSettings,
)

_Group = TypeVar("_Group", bound=BaseSettings)

_DOTENV_CHECK_S = 1.0


class _Dotenv:
    def __init__(self) -> None:
        self._seen: dict[str, tuple[float, int | None]] = {}

    def mtime(self, path: str) -> int | None:
        now = time.monotonic()
        checked = self._seen.get(path)
        if checked is not None and now - checked[0] < _DOTENV_CHECK_S:
            return checked[1]
        try:
            mtime: int | None = Path(path).stat().st_mtime_ns
        except OSError:  # silent: no .env file is a valid setup
            mtime = None
        self._seen[path] = (now, mtime)
        return mtime


class Settings:
    def __init__(self) -> None:
        self._built: dict[type[BaseSettings], tuple[int, BaseSettings]] = {}
        self._dotenv = _Dotenv()

    def _inputs(self, cls: type[BaseSettings]) -> int:
        env_file = cls.model_config.get("env_file")
        mtime = self._dotenv.mtime(str(env_file)) if env_file else None
        return hash((hash(frozenset(os.environ.items())), env_file, mtime))

    def _group(self, cls: type[_Group]) -> _Group:
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
    def threads(self) -> ThreadsSettings:
        return self._group(ThreadsSettings)

    @property
    def pinterest(self) -> PinterestSettings:
        return self._group(PinterestSettings)

    @property
    def distribution(self) -> DistributionSettings:
        return self._group(DistributionSettings)

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
    def sentry(self) -> SentrySettings:
        return self._group(SentrySettings)

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
    def server(self) -> ServerSettings:
        return self._group(ServerSettings)

    @property
    def events(self) -> EventsSettings:
        return self._group(EventsSettings)

    @property
    def telegram(self) -> TelegramSettings:
        return self._group(TelegramSettings)


settings = Settings()

__all__ = ["Settings", "settings"]
