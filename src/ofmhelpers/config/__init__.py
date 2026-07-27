"""
Single import point for app config: `from ofmhelpers.config import settings`.

`settings` is a lazy container -- each group property (settings.web,
settings.gdrive, ...) constructs that group's BaseSettings class fresh on
every access rather than caching one process-wide instance. This is
required, not just a style choice: several tests call
monkeypatch.setenv()/delenv() inside a test function body and expect the
very next call into application code to observe the new value (see
tests/test_auth.py, test_discord_client.py, test_gdrive_client.py,
test_gdrive_authorize.py, test_recovery.py, test_reel_machine_llm_registry.py).
A frozen `settings = Settings()` singleton would freeze whatever env values
existed at first import and silently ignore every later monkeypatch.

Call-site convention (mirrors how each var was read before this module
existed):
  - construct-once-at-import: assign `settings.<group>.<field>` to a
    module-level constant, same as the old `X = os.getenv(...)` pattern.
  - fresh-per-call: reference `settings.<group>.<field>` inline inside the
    function that needs it, every time -- never stash it in a module-level
    or long-lived variable.
"""

from ofmhelpers.config.settings import (
    DiscordSettings,
    DownloadersSettings,
    GDriveSettings,
    InfraSettings,
    InstagramStatsSettings,
    KieAISettings,
    LoggingSettings,
    ReelMachineSettings,
    SessionSettings,
    WebSettings,
)


class Settings:
    @property
    def session(self) -> SessionSettings:
        return SessionSettings()

    @property
    def web(self) -> WebSettings:
        return WebSettings()

    @property
    def infra(self) -> InfraSettings:
        return InfraSettings()

    @property
    def kieai(self) -> KieAISettings:
        return KieAISettings()

    @property
    def downloaders(self) -> DownloadersSettings:
        return DownloadersSettings()

    @property
    def discord(self) -> DiscordSettings:
        return DiscordSettings()

    @property
    def reel_machine(self) -> ReelMachineSettings:
        return ReelMachineSettings()

    @property
    def gdrive(self) -> GDriveSettings:
        return GDriveSettings()

    @property
    def instagram_stats(self) -> InstagramStatsSettings:
        return InstagramStatsSettings()

    @property
    def logging(self) -> LoggingSettings:
        return LoggingSettings()


settings = Settings()

__all__ = ["Settings", "settings"]
