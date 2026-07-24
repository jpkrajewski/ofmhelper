import pytest

from ofmhelpers.config.settings import (
    DiscordSettings,
    DownloadersSettings,
    GDriveSettings,
    KieAISettings,
    ReelMachineSettings,
    SessionSettings,
    WebSettings,
)
from ofmhelpers.web import jobs


@pytest.fixture(autouse=True)
def _isolated_jobs_store(monkeypatch, tmp_path):
    """web/jobs.py now persists every job to disk (see jobs.py) -- point it
    at a per-test temp file so the test suite never reads or writes the
    real uploads/jobs.json. JOBS itself (the in-memory dict) stays
    process-wide across tests, same as before this change; this only
    isolates the on-disk copy."""
    monkeypatch.setattr(jobs, "STORE_FILE", tmp_path / "jobs.json")


_SETTINGS_CLASSES = (
    SessionSettings,
    WebSettings,
    KieAISettings,
    DownloadersSettings,
    DiscordSettings,
    ReelMachineSettings,
    GDriveSettings,
)


@pytest.fixture(autouse=True)
def _no_real_dotenv_in_tests(monkeypatch):
    """Every settings group in ofmhelpers/config/settings.py has
    env_file=".env" for real app usage. Without this fixture, a test's
    monkeypatch.delenv("SOME_VAR") wouldn't actually make that var look
    unset -- pydantic-settings would still fall back to whatever real
    secret sits in the repo-root .env file (loaded relative to cwd, which
    is the repo root under `uv run pytest`), since env_file is a lower-
    priority source than the process environment, not something delenv
    can hide. That silently leaked real secrets into tests asserting
    "unset" behavior (e.g. test_recovery.py, test_discord_client.py).
    Blanking env_file for the whole test session makes monkeypatch fully
    authoritative, matching how a bare os.getenv() behaved before this
    module existed. config/settings.py's actual .env-loading behavior is
    covered directly by test_config_settings.py using an explicit
    _env_file=<temp file> override instead of the real one."""
    for cls in _SETTINGS_CLASSES:
        monkeypatch.setitem(cls.model_config, "env_file", None)
