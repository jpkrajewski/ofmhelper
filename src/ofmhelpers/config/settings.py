"""
Centralized scalar configuration (pydantic-settings). Single source of
truth for every env-var name/type/default in this app. Business/domain
data (SHAPES, LOOKS, GENDERS, TASK_LABELS, SCRAPRES_REGISTRY, etc.) is NOT
here -- see each owning module. The auth role names and the unauthenticated
path allowlist are the exception: they are deployment knobs (a reverse proxy
or a new public magic-link surface changes the allowlist), so they live in
WebSettings.

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
    """middleware/auth.py, api_keys.py, recovery.py, jobs.py,
    routers/generation/index.py, routers/downloads/index.py."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_password_admin: str | None = None
    app_password_va: str | None = None

    # The two roles the shared passwords resolve to -- every role check in the
    # app compares against these instead of a hand-typed literal (a typo'd
    # literal silently fails a role check instead of erroring).
    role_admin: str = Field(default="admin", validation_alias="OFM_ROLE_ADMIN")
    role_va: str = Field(default="va", validation_alias="OFM_ROLE_VA")

    # Paths reachable without being logged in. Keep both lists short and
    # explicit -- anything not listed is protected, which is the safe default
    # direction for an allowlist.
    #   /login, /health           -- the way in, and the deploy health probe
    #   /static/                  -- css/js/images
    #   /apply                    -- the landing page's application form POST,
    #                                see routers/apply.py (rate-limited, not
    #                                unguarded)
    public_paths: set[str] = Field(
        default={"/login", "/health", "/home", "/", "/apply"},
        validation_alias="OFM_PUBLIC_PATHS",
    )
    public_prefixes: tuple[str, ...] = Field(
        default=("/static/",), validation_alias="OFM_PUBLIC_PREFIXES"
    )
    kie_ai_api_key_admin: str | None = None
    kie_ai_api_key_va: str | None = None

    jobs_file: str = Field(
        default="uploads/jobs.json", validation_alias="OFM_JOBS_FILE"
    )

    max_jobs: int = Field(default=500, validation_alias="OFM_JOBS_MAX_ENTRIES")
    recovery_sweep_interval_s: int = Field(
        default=300, validation_alias="OFM_RECOVERY_SWEEP_INTERVAL_S"
    )
    gallery_limit: int = Field(default=20, validation_alias="OFM_GALLERY_LIMIT")
    # Panels per character sheet. The sheet is decoded whole in the worker's
    # memory, so this is what keeps one upload from exhausting it.
    character_sheet_max_images: int = Field(
        default=10, validation_alias="OFM_CHARACTER_SHEET_MAX_IMAGES"
    )

    # Rate limiting (web/middleware/ratelimit.py). The kill switch exists for the test
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

    # The reference-file picker (routers/refs.py). `GET /refs` with no limit
    # answers two short lists -- last picked, then newest uploaded -- and the
    # "Show older" button asks for an explicit limit, capped at max_ref_limit.
    recent_used_limit: int = Field(
        default=5, validation_alias="OFM_REFS_RECENT_USED_LIMIT"
    )
    recent_upload_limit: int = Field(
        default=5, validation_alias="OFM_REFS_RECENT_UPLOAD_LIMIT"
    )
    max_ref_limit: int = Field(default=60, validation_alias="OFM_REFS_MAX_LIMIT")
    # How many "recently picked" files web/ref_usage.py keeps in its Redis
    # sorted set. Only the first recent_used_limit are ever shown; the rest is
    # headroom so a file doesn't drop out the moment it stops being top-5.
    ref_usage_max_tracked: int = Field(
        default=200, validation_alias="OFM_REF_USAGE_MAX_TRACKED"
    )
    # Bounds the ffprobe/ffmpeg call behind a video reference thumbnail.
    ffmpeg_timeout_s: int = Field(default=20, validation_alias="OFM_FFMPEG_TIMEOUT_S")


class InfraSettings(BaseSettings):
    """Backing services shared by the API and the RQ worker: Postgres (the
    durable job/application store) and Redis (the RQ broker). Kept in one
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

    # Everything the app writes on behalf of a user lives under one root:
    # uploads/assets (the shared reference store), plus a subdir per tool.
    # Bind-mounted in both compose files, so a container swap keeps it.
    uploads_root: str = Field(default="uploads", validation_alias="OFM_UPLOADS_ROOT")
    # TTL on the repository cache-aside reads, see
    # web/db/repositories/cached_repository.py. Short: it
    # absorbs the read burst a page render makes, it is not a write-through
    # cache, and every write bumps its namespace anyway.
    cache_ttl_s: int = Field(default=300, validation_alias="OFM_CACHE_TTL_S")


class KieAISettings(BaseSettings):
    """aigenproviders/kaiai/client.py, routers/generation/fake_ai.py,
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
    # How long a "this local file is already hosted at that URL" entry is kept
    # in Redis (see client.upload_local_file). Well under kie.ai's own tempfile
    # retention, and every hit is confirmed live before it is trusted anyway,
    # so this only bounds how long a dead entry can keep costing one HEAD.
    upload_cache_ttl_s: int = Field(
        default=12 * 3600, validation_alias="OFM_KIEAI_UPLOAD_CACHE_TTL_S"
    )
    # Reference images over either limit are shrunk to a JPEG before upload
    # (client._fit_for_upload). Wan 3.0 documents 20MB / 8000px per side;
    # bytes are halved for the models whose limit is undocumented.
    ref_image_max_bytes: int = Field(
        default=10 * 1024 * 1024, validation_alias="OFM_KIEAI_REF_IMAGE_MAX_BYTES"
    )
    ref_image_max_side: int = Field(
        default=8000, validation_alias="OFM_KIEAI_REF_IMAGE_MAX_SIDE"
    )
    fake_ai_video_duration_seconds: int = Field(
        default=3, validation_alias="OFM_FAKE_AI_VIDEO_DURATION_SECONDS"
    )

    jobs_base: str = Field(
        default="https://api.kie.ai/api/v1/jobs", validation_alias="OFM_KIEAI_JOBS_BASE"
    )
    # A different host from the API: kie.ai serves uploads off its own CDN.
    upload_base: str = Field(
        default="https://kieai.redpandaai.co", validation_alias="OFM_KIEAI_UPLOAD_BASE"
    )

    # HTTP timeouts, split by what the call actually waits on: an upload
    # streams a whole reference file, a download streams a result, and the
    # task-queue calls are small JSON round trips.
    upload_timeout_s: int = Field(
        default=900, validation_alias="OFM_KIEAI_UPLOAD_TIMEOUT_S"
    )
    download_timeout_s: int = Field(
        default=60, validation_alias="OFM_KIEAI_DOWNLOAD_TIMEOUT_S"
    )
    request_timeout_s: int = Field(
        default=30, validation_alias="OFM_KIEAI_REQUEST_TIMEOUT_S"
    )
    # The best-effort HEAD behind the upload cache: it fails closed into a
    # re-upload, so waiting on it is never worth much.
    remote_check_timeout_s: int = Field(
        default=10, validation_alias="OFM_KIEAI_REMOTE_CHECK_TIMEOUT_S"
    )
    # How long an in-request poll waits before handing the task to the
    # recovery sweeper. kie.ai caps sane polling at 10-15 min.
    poll_timeout_s: int = Field(
        default=1000, validation_alias="OFM_KIEAI_POLL_TIMEOUT_S"
    )
    # Video takes materially longer than an image, so the two generation
    # wrappers wait different amounts before deferring to the sweeper.
    video_poll_timeout_s: int = Field(
        default=1800, validation_alias="OFM_KIEAI_VIDEO_POLL_TIMEOUT_S"
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
    # Bounds one gallery-dl run, which may pull a whole Instagram post set.
    image_download_timeout_s: int = Field(
        default=600, validation_alias="OFM_IMAGE_DOWNLOAD_TIMEOUT_S"
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


class LeadHuntSettings(BaseSettings):
    """scraping/lead_hunt.py + scraping/aggregators.py -- the Instagram
    creator-lead hunt (hashtag discovery -> profile enrich -> OnlyFans
    filter).

    Every field is something the operator retunes between runs (which
    hashtags cost how much, how small an account still counts as a lead,
    how patient the aggregator fetch is), so all of it is env-driven: no
    rebuild to widen a follower band or swap an Apify actor version."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Comma-separated.
    # Kept as a plain string rather than list[str]: pydantic-settings parses a
    # complex-typed field as JSON before any validator runs, which would make
    # `a,b` an error instead of two keys. `api_keys` does the split.
    apify_api_keys: str = Field(default="", validation_alias="OFM_APIFY_API_KEYS")

    # The band a lead has to fall in. The ceiling is the whole point of the
    # hunt (sub-10k accounts are the ones worth approaching); the floor is 0 by
    # default so it is a no-op until someone gets tired of 50-follower spam.
    max_followers: int = Field(
        default=10_000, validation_alias="OFM_LEADS_MAX_FOLLOWERS"
    )
    min_followers: int = Field(default=0, validation_alias="OFM_LEADS_MIN_FOLLOWERS")

    hashtag_actor_id: str = Field(
        default="apify/instagram-hashtag-scraper",
        validation_alias="OFM_LEADS_HASHTAG_ACTOR_ID",
    )
    profile_actor_id: str = Field(
        default="apify/instagram-profile-scraper",
        validation_alias="OFM_LEADS_PROFILE_ACTOR_ID",
    )
    # Posts pulled per hashtag. This is the credit dial: one post is one
    # candidate username before dedup, and dedup is brutal on a niche tag.
    posts_per_hashtag: int = Field(
        default=100, validation_alias="OFM_LEADS_POSTS_PER_HASHTAG"
    )
    # The profile actor is billed per username, so a run is capped rather than
    # allowed to follow a viral hashtag into thousands of profiles.
    max_profiles_per_run: int = Field(
        default=1000, validation_alias="OFM_LEADS_MAX_PROFILES_PER_RUN"
    )

    # Fetching a link-aggregator page (linktr.ee et al) to confirm the
    # OnlyFans link behind it. Short timeout and a hard read cap: this runs
    # once per candidate, and one slow or enormous page must not stall a run.
    aggregator_timeout_s: float = Field(
        default=10.0, validation_alias="OFM_LEADS_AGGREGATOR_TIMEOUT_S"
    )
    aggregator_max_bytes: int = Field(
        default=500_000, validation_alias="OFM_LEADS_AGGREGATOR_MAX_BYTES"
    )
    # Pause between aggregator fetches. These are small third-party sites, not
    # an API -- a run walks them politely rather than in a tight loop.
    aggregator_delay_s: float = Field(
        default=0.5, validation_alias="OFM_LEADS_AGGREGATOR_DELAY_S"
    )
    resolve_aggregators: bool = Field(
        default=True, validation_alias="OFM_LEADS_RESOLVE_AGGREGATORS"
    )

    @property
    def api_keys(self) -> list[str]:
        return [k.strip() for k in self.apify_api_keys.split(",") if k.strip()]
