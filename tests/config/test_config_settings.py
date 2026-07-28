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
    InfraSettings,
    InstagramStatsSettings,
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
    "OFM_DATABASE_URL",
    "OFM_REDIS_URL",
    "OFM_RQ_JOB_TIMEOUT_S",
    "OFM_RQ_ASYNC",
    "OFM_IG_STATS_LAST_N_POSTS",
    "OFM_IG_STATS_SUBPROCESS_TIMEOUT_S",
    "OFM_IG_STATS_NAV_TIMEOUT_MS",
    "OFM_IG_STATS_RENDER_WAIT_MS",
    "OFM_IG_STATS_REEL_RENDER_WAIT_MS",
    "OFM_IG_STATS_GRID_RETRY_WAIT_MS",
    "OFM_IG_STATS_SWEEP_HOUR_UTC",
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
    "REEL_MACHINE_PROMPT_FILE",
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


def test_infra_settings_defaults_point_at_compose_service_names(clean_env):
    s = InfraSettings(_env_file=None)
    assert s.database_url == (
        "postgresql+psycopg://ofmhelpers:ofmhelpers@postgres:5432/ofmhelpers"
    )
    assert s.redis_url == "redis://redis:6379/0"
    assert s.rq_job_timeout_s == 1800
    assert s.rq_async is True


def test_infra_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("OFM_DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("OFM_REDIS_URL", "redis://localhost:6380/1")
    s = InfraSettings(_env_file=None)
    assert s.database_url == "postgresql+psycopg://u:p@localhost/db"
    assert s.redis_url == "redis://localhost:6380/1"


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


def test_reel_machine_settings_defaults_to_gemini(clean_env):
    """Gemini is the only provider: the free API that takes the actual video
    rather than stills -- see reel_machine/llm/registry.py."""
    s = ReelMachineSettings(_env_file=None)
    assert s.llm_provider == "gemini"
    assert s.gemini_api_key is None
    assert s.gemini_model == "gemini-flash-latest"
    assert s.prompt_file == "uploads/analysis_prompt.txt"


def test_instagram_stats_settings_defaults_match_the_tuned_live_values(clean_env):
    """These are the waits/timeouts the live scrape was actually tuned to --
    a silent change here means empty grids or half-scraped accounts."""
    s = InstagramStatsSettings(_env_file=None)
    assert s.last_n_posts == 3
    assert s.subprocess_timeout_s == 120
    assert s.nav_timeout_ms == 30_000
    assert s.render_wait_ms == 3000
    assert s.reel_render_wait_ms == 2000
    assert s.grid_retry_wait_ms == 4000
    assert s.sweep_hour_utc == 0


def test_instagram_stats_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("OFM_IG_STATS_LAST_N_POSTS", "5")
    monkeypatch.setenv("OFM_IG_STATS_SWEEP_HOUR_UTC", "3")
    s = InstagramStatsSettings(_env_file=None)
    assert s.last_n_posts == 5
    assert s.sweep_hour_utc == 3


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
    assert not offenders, (
        f"os.getenv/os.environ found outside config/settings.py: {offenders}"
    )
