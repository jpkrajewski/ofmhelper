"""
Covers ofmhelpers/config/settings.py + config/__init__.py: the centralized
env-var/config layer every other module now reads through instead of
os.getenv/os.environ directly.
"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from ofmhelpers.config.settings import (
    DiscordSettings,
    DownloadersSettings,
    GDriveSettings,
    KieAISettings,
    ReelMachineSettings,
    SessionSettings,
    WebSettings,
)

ALL_ENV_VARS = [
    "SESSION_SECRET",
    "SESSION_HTTPS_ONLY",
    "APP_PASSWORD_ADMIN",
    "APP_PASSWORD_VA",
    "KIE_AI_API_KEY_ADMIN",
    "KIE_AI_API_KEY_VA",
    "APP_BASE_URL",
    "OFM_JOBS_FILE",
    "OFM_TODO_FILE",
    "OFM_APPROVAL_TOKENS_FILE",
    "OFM_JOBS_MAX_ENTRIES",
    "OFM_RECOVERY_SWEEP_INTERVAL_S",
    "OFM_APPROVAL_TOKEN_TTL_SECONDS",
    "OFM_GALLERY_LIMIT",
    "OFM_KIEAI_OUT_DIR",
    "OFM_KIEAI_TASK_LOG",
    "OFM_KIEAI_COMPLETIONS_LOG",
    "OFM_KIEAI_RESOLVED_LOG",
    "OFM_KIEAI_RESUME_MAX_AGE_S",
    "OFM_KIEAI_UPLOAD_CACHE_MAX_ENTRIES",
    "OFM_FAKE_AI_VIDEO_DURATION_SECONDS",
    "OFM_COOKIES_FILE",
    "BGUTIL_POT_PROVIDER_URL",
    "OFM_COOKIES_FROM_BROWSER",
    "DISCORD_WEBHOOK_URL",
    "REEL_MACHINE_LLM_PROVIDER",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "GROQ_VISION_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "HF_TOKEN",
    "HUGGINGFACE_TOKEN",
    "OFM_REEL_MACHINE_BEAT_GAP_S",
    "GOOGLE_OAUTH_CLIENT_FILE",
    "GOOGLE_DRIVE_TOKEN_FILE",
    "GOOGLE_DRIVE_FOLDER_ID",
]


@pytest.fixture
def clean_env(monkeypatch):
    """Clears every var this module knows about, so defaults tests never
    see the real repo-root .env leak in via the shared process environment."""
    for var in ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_session_settings_instantiates_with_sample_env(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "s3cr3t")
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "true")
    s = SessionSettings(_env_file=None)
    assert s.session_secret == "s3cr3t"
    assert s.session_https_only is True


def test_session_settings_missing_required_field_raises(clean_env):
    with pytest.raises(ValidationError):
        SessionSettings(_env_file=None)


def test_session_settings_wrong_type_raises(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "s3cr3t")
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "not-a-bool")
    with pytest.raises(ValidationError):
        SessionSettings(_env_file=None)


def test_web_settings_instantiates_with_sample_env(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_ADMIN", "admin-pw")
    monkeypatch.setenv("APP_BASE_URL", "https://example.com")
    monkeypatch.setenv("OFM_GALLERY_LIMIT", "42")
    s = WebSettings(_env_file=None)
    assert s.app_password_admin == "admin-pw"
    assert s.app_base_url == "https://example.com"
    assert s.gallery_limit == 42


def test_web_settings_wrong_type_raises(monkeypatch):
    monkeypatch.setenv("OFM_GALLERY_LIMIT", "not-a-number")
    with pytest.raises(ValidationError):
        WebSettings(_env_file=None)


def test_web_settings_defaults_match_pre_refactor_values(clean_env):
    s = WebSettings(_env_file=None)
    assert s.app_password_admin is None
    assert s.app_password_va is None
    assert s.kie_ai_api_key_admin is None
    assert s.kie_ai_api_key_va is None
    assert s.app_base_url is None
    assert s.jobs_file == "uploads/jobs.json"
    assert s.todo_file == "uploads/todos.json"
    assert s.approval_tokens_file == "uploads/approval_tokens.json"
    assert s.max_jobs == 500
    assert s.recovery_sweep_interval_s == 300
    assert s.approval_token_ttl_seconds == 3 * 24 * 3600
    assert s.gallery_limit == 20


def test_kieai_settings_defaults_match_pre_refactor_values(clean_env):
    s = KieAISettings(_env_file=None)
    assert s.out_dir == "/app/kieai_out"
    assert s.task_log == "/app/kieai_out/tasks.jsonl"
    assert s.completions_log == "/app/kieai_out/completions.jsonl"
    assert s.resolved_log == "/app/kieai_out/resolved.jsonl"
    assert s.resume_max_age_s == 48 * 3600
    assert s.upload_cache_max_entries == 100
    assert s.fake_ai_video_duration_seconds == 3


def test_downloaders_settings_defaults_match_pre_refactor_values(clean_env):
    s = DownloadersSettings(_env_file=None)
    assert s.cookies_file == "cookies/cookies.txt"
    assert s.bgutil_pot_provider_url is None
    assert s.cookies_from_browser is None


def test_discord_settings_defaults_match_pre_refactor_values(clean_env):
    s = DiscordSettings(_env_file=None)
    assert s.webhook_url is None


def test_reel_machine_settings_defaults_match_pre_refactor_values(clean_env):
    s = ReelMachineSettings(_env_file=None)
    assert s.llm_provider == "template"
    assert s.anthropic_api_key is None
    assert s.groq_api_key is None
    assert s.groq_vision_model is None
    assert s.gemini_api_key is None
    assert s.gemini_model == "gemini-flash-latest"
    assert s.hf_token is None
    assert s.huggingface_token is None
    assert s.beat_gap_s == 0.5


def test_gdrive_settings_defaults_match_pre_refactor_values(clean_env):
    s = GDriveSettings(_env_file=None)
    assert s.oauth_client_file == "secrets/google-oauth-client.json"
    assert s.token_file == "secrets/google-drive-token.json"
    assert s.folder_id is None


def test_env_file_override(tmp_path):
    env_file = tmp_path / "custom.env"
    env_file.write_text("OFM_KIEAI_OUT_DIR=/custom/out\n")
    s = KieAISettings(_env_file=env_file)
    assert s.out_dir == "/custom/out"


def test_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("OFM_COOKIES_FILE", "custom/cookies.txt")
    s = DownloadersSettings(_env_file=None)
    assert s.cookies_file == "custom/cookies.txt"


SRC = Path(__file__).resolve().parents[1] / "src" / "ofmhelpers"
ALLOWED_ENV_READ_DIR = SRC / "config"
ENV_READ_PATTERN = re.compile(r"os\.getenv\(|os\.environ\[|os\.environ\.get\(")


def test_no_stray_env_reads_outside_settings():
    offenders = []
    for path in SRC.rglob("*.py"):
        if ALLOWED_ENV_READ_DIR in path.parents or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if ENV_READ_PATTERN.search(text):
            offenders.append(str(path.relative_to(SRC)))
    assert (
        not offenders
    ), f"os.getenv/os.environ found outside config/settings.py: {offenders}"
