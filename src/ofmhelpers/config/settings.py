"""
Centralized scalar configuration (pydantic-settings). Single source of
truth for every env-var name/type/default in this app. Business/domain
data (SHAPES, LOOKS, GENDERS, TASK_LABELS, SCRAPRES_REGISTRY, PUBLIC_PATHS,
ROLE_ADMIN/VA, etc.) is NOT here -- see each owning module.

Grouped by subsystem rather than one flat class, and consumed via
`ofmhelpers.config.settings` (see config/__init__.py) whose group
properties construct their class fresh on every access. Never cache a
group instance across a monkeypatch boundary in a test, and never build a
module-level singleton of one of these classes directly -- see
config/__init__.py's docstring for why.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SessionSettings(BaseSettings):
    """Only ever constructed by web/main.py, at app-construction time.
    Deliberately its own class -- SESSION_SECRET is the one truly-required
    (no default) field in this app; keeping it isolated means no unrelated
    settings class construction (WebSettings, LLMSettings, ...) can ever
    fail because of it."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    session_secret: str
    session_https_only: bool = False
    # Cookie lifetime, and the deadline static/js/session.js counts down to
    # so an idle tab logs itself out instead of sitting there looking signed
    # in. Shared admin/VA passwords, so keep it short.
    session_max_age_s: int = 60 * 60 * 5  # 5 hours


class WebSettings(BaseSettings):
    """auth.py, recovery.py, jobs.py, todos.py, approval_tokens.py,
    routers/workflow/todo.py, routers/workflow/approve.py, routers/generation/index.py,
    routers/downloads/index.py."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_password_admin: str | None = None
    app_password_va: str | None = None
    kie_ai_api_key_admin: str | None = None
    kie_ai_api_key_va: str | None = None
    # One key for both roles, unlike kie.ai: ElevenLabs billing is per
    # workspace here, so there is nothing to split per role.
    elevenlabs_api_key: str | None = None
    app_base_url: str | None = None

    jobs_file: str = Field(
        default="uploads/jobs.json", validation_alias="OFM_JOBS_FILE"
    )
    todo_file: str = Field(
        default="uploads/todos.json", validation_alias="OFM_TODO_FILE"
    )
    approval_tokens_file: str = Field(
        default="uploads/approval_tokens.json",
        validation_alias="OFM_APPROVAL_TOKENS_FILE",
    )

    max_jobs: int = Field(default=500, validation_alias="OFM_JOBS_MAX_ENTRIES")
    recovery_sweep_interval_s: int = Field(
        default=300, validation_alias="OFM_RECOVERY_SWEEP_INTERVAL_S"
    )
    approval_token_ttl_seconds: int = Field(
        default=3 * 24 * 3600, validation_alias="OFM_APPROVAL_TOKEN_TTL_SECONDS"
    )
    gallery_limit: int = Field(default=20, validation_alias="OFM_GALLERY_LIMIT")

    # Rate limiting (web/ratelimit.py). The kill switch exists for the test
    # suite, which fires hundreds of POSTs from one client host -- leave it on
    # everywhere else.
    rate_limit_enabled: bool = Field(
        default=True, validation_alias="OFM_RATE_LIMIT_ENABLED"
    )
    # Failed logins per client IP before /login starts answering 429. Shared
    # passwords with no per-user lockout, so this is the only thing standing
    # between an attacker and unlimited guesses.
    login_max_failures: int = Field(
        default=10, validation_alias="OFM_LOGIN_MAX_FAILURES"
    )
    login_failure_window_s: int = Field(
        default=900, validation_alias="OFM_LOGIN_FAILURE_WINDOW_S"
    )
    # Blunt per-IP ceiling on every mutating request. Generous on purpose: a
    # legitimate multi-file upload burst must not hit it.
    write_rate_limit_requests: int = Field(
        default=120, validation_alias="OFM_WRITE_RATE_LIMIT_REQUESTS"
    )
    write_rate_limit_window_s: int = Field(
        default=60, validation_alias="OFM_WRITE_RATE_LIMIT_WINDOW_S"
    )


class InfraSettings(BaseSettings):
    """Backing services shared by the API and the RQ worker: Postgres (the
    durable job/todo/token store) and Redis (the RQ broker). Kept in one
    class so the worker process and the API read the exact same connection
    strings. Defaults point at the docker-compose service names, so a plain
    `docker compose up` wires everything with no extra env; override both for
    local-outside-Docker runs and for prod."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://ofmhelpers:ofmhelpers@postgres:5432/ofmhelpers",
        validation_alias="OFM_DATABASE_URL",
    )
    redis_url: str = Field(
        default="redis://redis:6379/0", validation_alias="OFM_REDIS_URL"
    )
    # Generation jobs poll kie.ai for up to ~15 min, far past RQ's 180s default
    # job timeout -- give the worker plenty of headroom before it kills a job.
    rq_job_timeout_s: int = Field(default=1800, validation_alias="OFM_RQ_JOB_TIMEOUT_S")
    # When False, enqueue() runs the job inline in the calling process instead
    # of handing it to the worker -- RQ's built-in synchronous mode. Prod runs
    # async (True); the test suite forces it False so TestClient sees the same
    # "background work already ran" behavior FastAPI BackgroundTasks gave.
    rq_async: bool = Field(default=True, validation_alias="OFM_RQ_ASYNC")
    # Worker-pool size. >=10 so at least 10 upload/poll/download jobs run
    # concurrently; read by ofmhelpers/worker.py, the worker entrypoint.
    rq_workers: int = Field(default=10, validation_alias="OFM_RQ_WORKERS")


class KieAISettings(BaseSettings):
    """aigenproviders/kaiai/client.py, upload_cache.py, routers/generation/fake_ai.py,
    routers/admin/file_manager.py."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    out_dir: str = Field(default="/app/kieai_out", validation_alias="OFM_KIEAI_OUT_DIR")
    task_log: str = Field(
        default="/app/kieai_out/tasks.jsonl", validation_alias="OFM_KIEAI_TASK_LOG"
    )
    completions_log: str = Field(
        default="/app/kieai_out/completions.jsonl",
        validation_alias="OFM_KIEAI_COMPLETIONS_LOG",
    )
    resolved_log: str = Field(
        default="/app/kieai_out/resolved.jsonl",
        validation_alias="OFM_KIEAI_RESOLVED_LOG",
    )
    resume_max_age_s: int = Field(
        default=48 * 3600, validation_alias="OFM_KIEAI_RESUME_MAX_AGE_S"
    )
    upload_cache_max_entries: int = Field(
        default=100, validation_alias="OFM_KIEAI_UPLOAD_CACHE_MAX_ENTRIES"
    )
    fake_ai_video_duration_seconds: int = Field(
        default=3, validation_alias="OFM_FAKE_AI_VIDEO_DURATION_SECONDS"
    )


class DownloadersSettings(BaseSettings):
    """downloaders/cookies.py, downloaders/generic.py, routers/admin/cookies.py."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cookies_file: str = Field(
        default="cookies/cookies.txt", validation_alias="OFM_COOKIES_FILE"
    )
    bgutil_pot_provider_url: str | None = None
    cookies_from_browser: str | None = Field(
        default=None, validation_alias="OFM_COOKIES_FROM_BROWSER"
    )


class DiscordSettings(BaseSettings):
    """discord/client.py. Constructed fresh, every call, inside
    send_webhook() -- see tests/test_discord_client.py."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    webhook_url: str | None = Field(
        default=None, validation_alias="DISCORD_WEBHOOK_URL"
    )


class ReelMachineSettings(BaseSettings):
    """reel_machine/llm/*.py -- which model watches the reel, and its key."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The analysis prompt is the one thing here that gets retuned by reading
    # bad output and rewriting a sentence, so it lives in a file under the
    # bind-mounted uploads/ dir rather than only in the image: edit it on the
    # server and the next job uses it, no rebuild. Missing file = the frozen
    # default in reel_machine/prompts.py.
    prompt_file: str = Field(
        default="uploads/analysis_prompt.txt",
        validation_alias="REEL_MACHINE_PROMPT_FILE",
    )

    # "gemini" is the only provider -- see reel_machine/llm/registry.py. The
    # field stays because an unknown name has to raise rather than silently
    # run something nobody picked.
    llm_provider: str = Field(
        default="gemini", validation_alias="REEL_MACHINE_LLM_PROVIDER"
    )
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"

    # The second, text-only pass (reel_machine/hunt.py): Gemini describes the
    # reel, then this free model turns that description into hashtags, search
    # phrases and outfit alternatives. Optional on purpose -- no key means the
    # review page falls back to terms derived from the analysis itself.
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"


class InstagramStatsSettings(BaseSettings):
    """scraping/instagram_public.py + scraping/instagram_stats_job.py -- the
    free, no-login Playwright scrape behind the /models page's follower and
    last-N-reels numbers.

    Every field here is a knob that has to be retuned when Instagram changes
    (page render speed, how aggressively it soft-blocks scrapers), which is
    exactly the case for an env var: retune on the server without a rebuild.
    Selectors and regexes are NOT here -- those are code, not configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    last_n_posts: int = Field(default=3, validation_alias="OFM_IG_STATS_LAST_N_POSTS")
    # The scrape runs as a subprocess (see instagram_public.py's docstring);
    # this bounds the whole per-account scrape, not one page load.
    subprocess_timeout_s: int = Field(
        default=120, validation_alias="OFM_IG_STATS_SUBPROCESS_TIMEOUT_S"
    )
    nav_timeout_ms: int = Field(
        default=30_000, validation_alias="OFM_IG_STATS_NAV_TIMEOUT_MS"
    )
    # Instagram client-renders the profile header and the reels grid after
    # domcontentloaded, so every goto is followed by a fixed settle wait.
    render_wait_ms: int = Field(
        default=3000, validation_alias="OFM_IG_STATS_RENDER_WAIT_MS"
    )
    reel_render_wait_ms: int = Field(
        default=2000, validation_alias="OFM_IG_STATS_REEL_RENDER_WAIT_MS"
    )
    # One extra wait before giving up on an empty grid -- hydration is
    # timing-sensitive (cookie banner, first-load jank).
    grid_retry_wait_ms: int = Field(
        default=4000, validation_alias="OFM_IG_STATS_GRID_RETRY_WAIT_MS"
    )
    # Hour (UTC) the nightly sweep runs at; the sweep re-queues its own next
    # run, so a change takes effect from the run after next.
    sweep_hour_utc: int = Field(
        default=0, validation_alias="OFM_IG_STATS_SWEEP_HOUR_UTC"
    )


class LoggingSettings(BaseSettings):
    """Read once per process by ofmhelpers.logging.configure_logging(), which
    every entrypoint (web/main.py, the RQ worker, alembic/env.py) calls before
    doing any work. Not read anywhere else -- modules just call
    get_logger(__name__)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    level: str = Field(default="INFO", validation_alias="OFM_LOG_LEVEL")
    # "text" is readable in `docker compose logs`; "json" emits one JSON object
    # per line for a log aggregator that parses structured fields.
    format: str = Field(default="text", validation_alias="OFM_LOG_FORMAT")
    # Uvicorn's per-request access log is noise once a reverse proxy in front
    # is already logging the same requests.
    access_log: bool = Field(default=True, validation_alias="OFM_LOG_ACCESS")


class GDriveSettings(BaseSettings):
    """gdrive/authorize.py, gdrive/client.py. Constructed fresh, every
    call -- see tests/test_gdrive_client.py / test_gdrive_authorize.py."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    oauth_client_file: str = Field(
        default="secrets/google-oauth-client.json",
        validation_alias="GOOGLE_OAUTH_CLIENT_FILE",
    )
    token_file: str = Field(
        default="secrets/google-drive-token.json",
        validation_alias="GOOGLE_DRIVE_TOKEN_FILE",
    )
    folder_id: str | None = Field(
        default=None, validation_alias="GOOGLE_DRIVE_FOLDER_ID"
    )
