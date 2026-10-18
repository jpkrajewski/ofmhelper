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

from pydantic import AliasChoices, Field
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

    max_jobs: int = Field(default=500, validation_alias="OFM_JOBS_MAX_ENTRIES")
    recovery_sweep_interval_s: int = Field(
        default=300, validation_alias="OFM_RECOVERY_SWEEP_INTERVAL_S"
    )
    gallery_limit: int = Field(default=20, validation_alias="OFM_GALLERY_LIMIT")
    # How often /generate and /clone ask for the newest jobs (live-gallery.js),
    # so a coworker's run shows up without a reload.
    gallery_live_poll_s: int = Field(
        default=5, ge=1, validation_alias="OFM_GALLERY_LIVE_POLL_S"
    )
    # Downloaded results are saved camera-roll style -- prefix + 4 random
    # digits (IMG_4821.png, VID_0193.mp4) -- not under the generator's name.
    image_download_prefix: str = Field(
        default="IMG_", validation_alias="OFM_IMAGE_DOWNLOAD_PREFIX"
    )
    video_download_prefix: str = Field(
        default="VID_", validation_alias="OFM_VIDEO_DOWNLOAD_PREFIX"
    )
    download_name_digits: int = Field(
        default=4, ge=1, validation_alias="OFM_DOWNLOAD_NAME_DIGITS"
    )
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
    # /uniqify (routers/video_tools/uniqify.py): copies per run, encoded one
    # after another on one worker, and the bound on each copy's ffmpeg encode.
    uniqify_max_copies: int = Field(
        default=50, validation_alias="OFM_UNIQIFY_MAX_COPIES"
    )
    # Longest random head-trim /uniqify offers, in seconds.
    uniqify_max_trim_s: float = Field(
        default=5.0, gt=0, validation_alias="OFM_UNIQIFY_MAX_TRIM_S"
    )
    uniqify_encode_timeout_s: int = Field(
        default=600, validation_alias="OFM_UNIQIFY_ENCODE_TIMEOUT_S"
    )
    # /clone (routers/generation/clone.py): videos per submit -- each one is a
    # paid Wan 3.0 run, so this caps what one mis-click can spend -- and how
    # many recent clone jobs the page groups into batches.
    clone_max_videos: int = Field(default=20, validation_alias="OFM_CLONE_MAX_VIDEOS")
    # Subject photos per clone batch (same person, different angles), all
    # sent to every run. Wan 3.0 takes at most 10 reference images.
    clone_max_images: int = Field(
        default=10, ge=1, le=10, validation_alias="OFM_CLONE_MAX_IMAGES"
    )
    # The /clone form's starting prompt.
    clone_default_prompt: str = Field(
        default="Replace the subject in @Video1 with subject from @Image1",
        validation_alias="OFM_CLONE_DEFAULT_PROMPT",
    )
    # ffprobe length checks /clone/run runs at once (one per picked video).
    clone_probe_workers: int = Field(
        default=8, ge=1, validation_alias="OFM_CLONE_PROBE_WORKERS"
    )
    # Bound on the CapCut re-encode (utils/capcut.py) of one generated clone.
    clone_capcut_timeout_s: int = Field(
        default=600, validation_alias="OFM_CLONE_CAPCUT_TIMEOUT_S"
    )
    clone_gallery_limit: int = Field(
        default=60, validation_alias="OFM_CLONE_GALLERY_LIMIT"
    )
    # The /clone form's live cost estimate: credits one clone run costs per
    # second of its source video, by resolution. Measured from this
    # account's Wan 3.0 history (2026-10-17: 81 runs at 720P, 14 at 1080P;
    # kie.ai bills the output plus the reference video, so about twice its
    # 16 / 32 credits a second). 480P has no runs yet: half of 720P, as on
    # kie.ai's price list. JSON in the env var.
    clone_credits_per_video_s: dict[str, float] = Field(
        default={"480P": 16.39, "720P": 32.78, "1080P": 64.0},
        validation_alias="OFM_CLONE_CREDITS_PER_VIDEO_S",
    )
    # What one kie.ai credit costs in dollars: $50 buys 10,000.
    kie_usd_per_credit: float = Field(
        default=0.005, gt=0, validation_alias="OFM_KIE_USD_PER_CREDIT"
    )
    # The same estimate on Higgsfield. Its Wan 3.0 list price is $0.05 /
    # $0.10 / $0.20 per billed second (480p/720p/1080p), billed = output +
    # reference video seconds, so about twice the source length; this
    # account gets 60% off. Measured 2026-10-18: a 13.09 s source at 720p
    # billed 26.09 s and cost $1.05, i.e. $0.04 a billed second.
    clone_hf_credits_per_video_s: dict[str, float] = Field(
        default={"480P": 0.64, "720P": 1.28, "1080P": 2.56},
        validation_alias="OFM_CLONE_HF_CREDITS_PER_VIDEO_S",
    )
    # What one Higgsfield credit costs in dollars: 16 credits = $1.
    hf_usd_per_credit: float = Field(
        default=0.0625, gt=0, validation_alias="OFM_HF_USD_PER_CREDIT"
    )


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
    # A video generation job waits up to video_poll_timeout_s (30 min) on kie.ai
    # alone, then downloads, and /clone may CapCut re-encode it (up to
    # clone_capcut_timeout_s) -- far past RQ's 180s default. 45 min covers all
    # of it, so a slow-but-finished generation is never killed mid-way.
    rq_job_timeout_s: int = Field(default=2700, validation_alias="OFM_RQ_JOB_TIMEOUT_S")
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
    """aigenproviders/kaiai/: kie.ai's endpoints, upload limits and audit
    logs. What every provider shares (output dir, polling) is AigenSettings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    task_log: str = Field(
        default="/app/kieai_out/tasks.jsonl", validation_alias="OFM_KIEAI_TASK_LOG"
    )
    completions_log: str = Field(
        default="/app/kieai_out/completions.jsonl",
        validation_alias="OFM_KIEAI_COMPLETIONS_LOG",
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
    request_timeout_s: int = Field(
        default=30, validation_alias="OFM_KIEAI_REQUEST_TIMEOUT_S"
    )
    # The best-effort HEAD behind the upload cache: it fails closed into a
    # re-upload, so waiting on it is never worth much.
    remote_check_timeout_s: int = Field(
        default=10, validation_alias="OFM_KIEAI_REMOTE_CHECK_TIMEOUT_S"
    )


class AigenSettings(BaseSettings):
    """aigenproviders/runner.py, aigenproviders/tasks.py, web/recovery.py:
    what every provider shares -- where results land, how long a poll waits
    before handing the job to recovery, and how long recovery keeps trying.

    The output dir keeps reading OFM_KIEAI_OUT_DIR so results stay where
    kie.ai's always went (the file manager and fake AI model read it too)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    out_dir: str = Field(
        default="/app/kieai_out",
        validation_alias=AliasChoices("OFM_AIGEN_OUT_DIR", "OFM_KIEAI_OUT_DIR"),
    )
    poll_timeout_s: int = Field(
        default=1000,
        validation_alias=AliasChoices(
            "OFM_AIGEN_POLL_TIMEOUT_S", "OFM_KIEAI_POLL_TIMEOUT_S"
        ),
    )
    video_poll_timeout_s: int = Field(
        default=1800,
        validation_alias=AliasChoices(
            "OFM_AIGEN_VIDEO_POLL_TIMEOUT_S", "OFM_KIEAI_VIDEO_POLL_TIMEOUT_S"
        ),
    )
    poll_interval_s: float = Field(
        default=2.5, validation_alias="OFM_AIGEN_POLL_INTERVAL_S"
    )
    poll_max_interval_s: float = Field(
        default=20.0, validation_alias="OFM_AIGEN_POLL_MAX_INTERVAL_S"
    )
    download_timeout_s: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "OFM_AIGEN_DOWNLOAD_TIMEOUT_S", "OFM_KIEAI_DOWNLOAD_TIMEOUT_S"
        ),
    )
    # A provider's result URLs outlive this (kie.ai ~24h, Higgsfield 7 days),
    # so a job recovery has not finished by then is written off.
    resume_max_age_s: int = Field(
        default=48 * 3600,
        validation_alias=AliasChoices(
            "OFM_AIGEN_RESUME_MAX_AGE_S", "OFM_KIEAI_RESUME_MAX_AGE_S"
        ),
    )
    # The fake provider's placeholder video length.
    fake_video_duration_s: int = Field(
        default=3, validation_alias="OFM_FAKE_AI_VIDEO_DURATION_SECONDS"
    )


class HiggsfieldSettings(BaseSettings):
    """aigenproviders/higgsfield/. Keys are Higgsfield's `KEY_ID:KEY_SECRET`
    pair as one string, pre-filled per role like the kie.ai keys; unset just
    means the form's key field starts empty."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key_admin: str | None = Field(default=None, validation_alias="HF_API_KEY_ADMIN")
    api_key_va: str | None = Field(default=None, validation_alias="HF_API_KEY_VA")
    base_url: str = Field(
        default="https://api.higgsfield.ai", validation_alias="OFM_HF_BASE_URL"
    )
    # One SDK call (submit, status, presign, upload PUT); an upload streams
    # a whole reference video, so this is generous.
    request_timeout_s: float = Field(
        default=300, validation_alias="OFM_HF_REQUEST_TIMEOUT_S"
    )
    upload_cache_ttl_s: int = Field(
        default=24 * 3600, validation_alias="OFM_HF_UPLOAD_CACHE_TTL_S"
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


class InstagramSettings(BaseSettings):
    """instagram/client.py, routers/downloads/reels.py, web/reel_watch.py."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The public web app's id; Instagram's web API rejects calls without it.
    app_id: str = Field(default="936619743392459", validation_alias="OFM_IG_APP_ID")
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
        ),
        validation_alias="OFM_IG_USER_AGENT",
    )
    asbd_id: str = Field(default="359341", validation_alias="OFM_IG_ASBD_ID")
    # The logged-out web GraphQL queries instagram/client.py runs. Instagram
    # rotates these every few weeks; a fresh one is in instagram.com's JS
    # (gallery-dl's instagram extractor tracks them too).
    profile_doc_id: str = Field(
        default="28036671149327607", validation_alias="OFM_IG_PROFILE_DOC_ID"
    )
    reels_doc_id: str = Field(
        default="28989741540630190", validation_alias="OFM_IG_REELS_DOC_ID"
    )
    media_doc_id: str = Field(
        default="27130156389949648", validation_alias="OFM_IG_MEDIA_DOC_ID"
    )
    timeline_doc_id: str = Field(
        default="28142289515441884", validation_alias="OFM_IG_TIMELINE_DOC_ID"
    )
    # /reels feed: cards per page (the rest load on scroll).
    feed_page_size: int = Field(
        default=60, ge=1, validation_alias="OFM_IG_FEED_PAGE_SIZE"
    )
    timeout_s: int = Field(default=20, validation_alias="OFM_IG_TIMEOUT_S")
    # Proxies the logged-out Instagram calls go through, comma-separated,
    # "user:pass@host:port" or a full URL. Each profile scan takes one; a
    # rate-limited profile is retried on the next. Empty: direct. Secrets --
    # .env only, never committed. The logged-in (cookies.txt) calls never
    # use them: one account seen from many IPs is what trips a checkpoint.
    proxies: str = Field(default="", validation_alias="OFM_IG_PROXIES")
    # Proxies a rate-limited profile is retried on before a batch stops.
    proxy_retries: int = Field(default=3, ge=0, validation_alias="OFM_IG_PROXY_RETRIES")
    # A random pause up to this long before a profile is retried on the next
    # proxy, so retries don't land on Instagram in lockstep.
    retry_jitter_s: float = Field(
        default=2.0, ge=0, validation_alias="OFM_IG_RETRY_JITTER_S"
    )
    # Each call's own retries of a dropped connection, a read timeout or a
    # 5xx (exponential backoff from retry_backoff_s, plus as much jitter),
    # before the proxy rotation takes over.
    http_retries: int = Field(default=2, ge=0, validation_alias="OFM_IG_HTTP_RETRIES")
    retry_backoff_s: float = Field(
        default=0.5, ge=0, validation_alias="OFM_IG_RETRY_BACKOFF_S"
    )

    @property
    def proxy_urls(self) -> list[str]:
        return [
            p if "://" in p else f"http://{p}"
            for p in (part.strip() for part in self.proxies.split(","))
            if p
        ]

    # A scan looks at every reel posted in the last max_age_days, fetched
    # page_size at a time; reels_per_profile caps it for a profile posting
    # dozens a day.
    max_age_days: float = Field(
        default=14, gt=0, validation_alias="OFM_IG_MAX_AGE_DAYS"
    )
    reels_per_profile: int = Field(
        default=120, ge=1, validation_alias="OFM_IG_REELS_PER_PROFILE"
    )
    page_size: int = Field(default=12, ge=1, validation_alias="OFM_IG_PAGE_SIZE")
    # /reels: a reel is listed from min_views up (the lowest tier, see
    # instagram/models.py's VIEW_TIERS), and anything longer than
    # max_duration_s never is. 30 s is the most Wan 3.0 (what /clone runs)
    # takes.
    min_views: int = Field(default=50_000, ge=0, validation_alias="OFM_IG_MIN_VIEWS")
    max_duration_s: float = Field(
        default=30.0, gt=0, validation_alias="OFM_IG_MAX_DURATION_S"
    )
    # web/reel_watch.py: how stale a watched profile's scan may get, how
    # often the sweeper looks for stale ones, and how long a claimed scan
    # may run before another sweep takes it over.
    rescan_interval_s: int = Field(
        default=12 * 60 * 60, validation_alias="OFM_IG_RESCAN_INTERVAL_S"
    )
    rescan_check_interval_s: int = Field(
        default=600, validation_alias="OFM_IG_RESCAN_CHECK_INTERVAL_S"
    )
    rescan_claim_ttl_s: int = Field(
        default=60 * 60, validation_alias="OFM_IG_RESCAN_CLAIM_TTL_S"
    )
    # Reel mp4 downloads "Clone selected" runs at once.
    download_workers: int = Field(
        default=6, ge=1, validation_alias="OFM_IG_DOWNLOAD_WORKERS"
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
