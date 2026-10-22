"""
Covers ofmhelpers/config/settings.py + config/__init__.py: the centralized
env-var/config layer every other module now reads through instead of
os.getenv/os.environ directly.
"""

import os
import re

import pytest
from pydantic import ValidationError
from repo_paths import SRC

from ofmhelpers.config.settings import (
    AigenSettings,
    DownloadersSettings,
    InfraSettings,
    KieAISettings,
    SessionSettings,
    WebSettings,
)

ALL_ENV_VARS = [
    "OFM_AIGEN_OUT_DIR",
    "OFM_AIGEN_POLL_TIMEOUT_S",
    "OFM_AIGEN_VIDEO_POLL_TIMEOUT_S",
    "OFM_AIGEN_DOWNLOAD_TIMEOUT_S",
    "OFM_AIGEN_RESUME_MAX_AGE_S",
    "SESSION_SECRET",
    "SESSION_HTTPS_ONLY",
    "APP_PASSWORD_ADMIN",
    "APP_PASSWORD_VA",
    "KIE_AI_API_KEY_ADMIN",
    "KIE_AI_API_KEY_VA",
    "OFM_JOBS_MAX_ENTRIES",
    "OFM_RECOVERY_SWEEP_INTERVAL_S",
    "OFM_GALLERY_LIMIT",
    "OFM_DATABASE_URL",
    "OFM_REDIS_URL",
    "OFM_RQ_JOB_TIMEOUT_S",
    "OFM_RQ_ASYNC",
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
    "HF_TOKEN",
    "HUGGINGFACE_TOKEN",
    # ... and the ones that used to be literals in library code.
    "OFM_UPLOADS_ROOT",
    "OFM_CACHE_TTL_S",
    "OFM_REFS_RECENT_USED_LIMIT",
    "OFM_REFS_RECENT_UPLOAD_LIMIT",
    "OFM_REFS_MAX_LIMIT",
    "OFM_REF_USAGE_MAX_TRACKED",
    "OFM_FFMPEG_TIMEOUT_S",
    "OFM_KIEAI_JOBS_BASE",
    "OFM_KIEAI_UPLOAD_BASE",
    "OFM_KIEAI_UPLOAD_TIMEOUT_S",
    "OFM_KIEAI_DOWNLOAD_TIMEOUT_S",
    "OFM_KIEAI_REQUEST_TIMEOUT_S",
    "OFM_KIEAI_REMOTE_CHECK_TIMEOUT_S",
    "OFM_KIEAI_POLL_TIMEOUT_S",
    "OFM_KIEAI_VIDEO_POLL_TIMEOUT_S",
    "OFM_IMAGE_DOWNLOAD_TIMEOUT_S",
    "GROQ_URL",
    "GROQ_TIMEOUT_S",
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
    monkeypatch.setenv("OFM_GALLERY_LIMIT", "42")
    s = WebSettings(_env_file=None)
    assert s.app_password_admin == "admin-pw"
    assert s.gallery_limit == 42


def test_web_settings_wrong_type_raises(monkeypatch):
    monkeypatch.setenv("OFM_GALLERY_LIMIT", "not-a-number")
    with pytest.raises(ValidationError):
        WebSettings(_env_file=None)


def test_web_settings_defaults_match_pre_refactor_values(clean_env):
    s = WebSettings(_env_file=None)
    assert s.app_password_admin is None
    assert s.kie_ai_api_key_admin is None
    assert s.max_jobs == 500
    assert s.recovery_sweep_interval_s == 300
    assert s.gallery_limit == 20


def test_infra_settings_defaults_point_at_compose_service_names(clean_env):
    s = InfraSettings(_env_file=None)
    assert s.database_url == (
        "postgresql+psycopg://ofmhelpers:ofmhelpers@postgres:5432/ofmhelpers"
    )
    assert s.redis_url == "redis://redis:6379/0"
    assert s.rq_job_timeout_s == 2700
    assert s.rq_async is True


def test_infra_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("OFM_DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("OFM_REDIS_URL", "redis://localhost:6380/1")
    s = InfraSettings(_env_file=None)
    assert s.database_url == "postgresql+psycopg://u:p@localhost/db"
    assert s.redis_url == "redis://localhost:6380/1"


def test_kieai_settings_defaults_match_pre_refactor_values(clean_env):
    s = KieAISettings(_env_file=None)
    assert s.task_log == "/app/kieai_out/tasks.jsonl"
    assert s.completions_log == "/app/kieai_out/completions.jsonl"
    assert s.upload_cache_ttl_s == 12 * 3600


def test_aigen_settings_keep_the_kie_era_defaults(clean_env):
    """What moved from KieAISettings to the provider-shared group kept its
    default, so a deployment that sets nothing behaves as before."""
    s = AigenSettings(_env_file=None)
    assert s.out_dir == "/app/kieai_out"
    assert s.resume_max_age_s == 48 * 3600
    assert (s.poll_timeout_s, s.video_poll_timeout_s) == (1000, 1800)
    assert s.download_timeout_s == 60
    assert s.fake_video_duration_s == 3


def test_aigen_settings_still_read_the_old_kie_env_names(monkeypatch):
    monkeypatch.delenv("OFM_AIGEN_OUT_DIR", raising=False)
    monkeypatch.setenv("OFM_KIEAI_OUT_DIR", "/old/name")
    monkeypatch.setenv("OFM_KIEAI_POLL_TIMEOUT_S", "7")
    s = AigenSettings(_env_file=None)
    assert (s.out_dir, s.poll_timeout_s) == ("/old/name", 7)


def test_downloaders_settings_defaults_match_pre_refactor_values(clean_env):
    s = DownloadersSettings(_env_file=None)
    assert s.cookies_file == "cookies/cookies.txt"
    assert s.bgutil_pot_provider_url is None
    assert s.cookies_from_browser is None


def test_env_file_override(tmp_path, monkeypatch):
    monkeypatch.delenv("OFM_AIGEN_OUT_DIR", raising=False)
    env_file = tmp_path / "custom.env"
    env_file.write_text("OFM_AIGEN_OUT_DIR=/custom/out\n")
    s = AigenSettings(_env_file=env_file)
    assert s.out_dir == "/custom/out"


def test_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("OFM_COOKIES_FILE", "custom/cookies.txt")
    s = DownloadersSettings(_env_file=None)
    assert s.cookies_file == "custom/cookies.txt"


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


# ── Phase 3: values that used to be literals in library code ────────────────
#
# The point of each assertion below is that the *default* still equals what the
# hardcoded literal was, so moving it into settings changed no behaviour for a
# deployment that sets nothing.


def test_kieai_endpoints_and_timeouts_default_to_the_previous_literals(clean_env):
    s = KieAISettings(_env_file=None)
    assert s.jobs_base == "https://api.kie.ai/api/v1/jobs"
    assert s.upload_base == "https://kieai.redpandaai.co"
    assert s.upload_timeout_s == 900
    assert (s.request_timeout_s, s.remote_check_timeout_s) == (30, 10)


def test_web_picker_limits_default_to_the_previous_literals(clean_env):
    s = WebSettings(_env_file=None)
    assert (s.recent_used_limit, s.recent_upload_limit) == (5, 5)
    assert (s.max_ref_limit, s.ref_usage_max_tracked) == (60, 200)
    assert s.ffmpeg_timeout_s == 20


def test_uploads_root_and_cache_ttl_default_to_the_previous_literals(clean_env):
    s = InfraSettings(_env_file=None)
    assert s.uploads_root == "uploads"
    assert s.cache_ttl_s == 300


def test_uploads_root_is_overridable(monkeypatch):
    """Every per-tool upload dir derives from this one, so an override has to
    reach all of them -- see routers/task_helpers.UPLOADS_ROOT."""
    monkeypatch.setenv("OFM_UPLOADS_ROOT", "/data/uploads")
    assert InfraSettings(_env_file=None).uploads_root == "/data/uploads"


def test_downloader_timeout_defaults_to_the_previous_literal(clean_env):
    assert DownloadersSettings(_env_file=None).image_download_timeout_s == 600


# --- the `settings` container: built once per environment ---------------------


def test_a_group_is_built_once_while_the_environment_holds(monkeypatch):
    """Building a group reads .env and validates every field: /reels did it
    310 times per request (prod, 2026-10-22)."""
    from ofmhelpers.config import Settings

    monkeypatch.setenv("OFM_GALLERY_LIMIT", "7")
    s = Settings()
    assert s.web is s.web
    assert s.web.gallery_limit == 7


def test_a_group_follows_a_changed_environment_at_once(monkeypatch):
    from ofmhelpers.config import Settings

    s = Settings()
    monkeypatch.setenv("OFM_GALLERY_LIMIT", "7")
    first = s.web
    monkeypatch.setenv("OFM_GALLERY_LIMIT", "9")
    assert s.web.gallery_limit == 9
    assert s.web is not first
    monkeypatch.delenv("OFM_GALLERY_LIMIT")
    assert s.web.gallery_limit == 20


def test_a_group_follows_an_edited_env_file(monkeypatch, tmp_path):
    from ofmhelpers import config
    from ofmhelpers.config import Settings

    monkeypatch.delenv("OFM_GALLERY_LIMIT", raising=False)
    monkeypatch.setattr(config, "_DOTENV_CHECK_S", 0.0)
    dotenv = tmp_path / ".env"
    dotenv.write_text("OFM_GALLERY_LIMIT=5\n")
    monkeypatch.setitem(WebSettings.model_config, "env_file", str(dotenv))
    s = Settings()
    assert s.web.gallery_limit == 5

    dotenv.write_text("OFM_GALLERY_LIMIT=6\n")
    os.utime(dotenv, ns=(1, 10**18))
    assert s.web.gallery_limit == 6
