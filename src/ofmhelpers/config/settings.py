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

import datetime
from enum import IntEnum, StrEnum
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    SettingsConfigDict,
)

from ofmhelpers.media.image_fit import ImageLimits
from ofmhelpers.media.recorder.models import GeoPoint, Place

_DAILY_AT_MSG = "rescan_daily_at must be HH:MM"
# Pydantic-settings lowercases the names it reads.
_STACKED_KEY_PREFIX = "stacked_api_key_"


class QueueName(StrEnum):
    """The background job queues (`cache/queue.py`), first in first out, highest
    priority first. A new one: a member here, its entry in `InfraSettings.queues`,
    its compose service, both deploys (tests/unit/test_worker_services.py)."""

    # Generations, downloads, uploads: the queue a job is on unless it says otherwise.
    DEFAULT = "default"
    # /clone-links runs (features/clone_links/run.py): a 30-120 link Generate on
    # its own worker pool, so it never holds the default queue for hours.
    CLONE_LINKS = "clone-links"
    # Gallery previews (features/previews/render.py): ffmpeg encodes, on their own
    # low-priority worker so they never hold a generation worker.
    PREVIEWS = "previews"
    # Local encodes of big videos (/trials copies), one at a time: a 4K one holds
    # 1.5 GB, and six at once on prod's 7.75 GB had the kernel killing them
    # (2026-11-04).
    MEDIA = "media"


# Every queue `run_job` is put on: what `cache.active_job_ids` reads.
JOB_QUEUES = (QueueName.DEFAULT, QueueName.CLONE_LINKS, QueueName.MEDIA)


class QueuePriority(IntEnum):
    """Who gets the CPU when the box is busy: a queue's worker processes, and the
    ffmpeg they start, run at this Linux nice value (bigger = further back)."""

    HIGHEST = 0
    HIGH = 5
    LOW = 15
    LOWEST = 19


class QueueConfig(BaseModel):
    """`workers`: how many of the queue's jobs run at once."""

    model_config = ConfigDict(frozen=True)

    workers: int
    priority: QueuePriority


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
    # The public address, with a trailing slash (https://ascendglobal.cash/):
    # what links leaving the app (OAuth redirects) are built from. Empty:
    # the request's own address.
    base_url: str = Field(default="", validation_alias="APP_BASE_URL")

    # Paths reachable without being logged in. Keep both lists short and
    # explicit -- anything not listed is protected, which is the safe default
    # direction for an allowlist.
    #   /login, /health           -- the way in, and the deploy health probe
    #   /static/                  -- css/js/images
    #   /auth/, /password/        -- Google/Apple sign-in round trips and
    #                                set-password links (routers/auth.py)
    #   /apply                    -- the landing page's application form POST,
    #                                see routers/apply.py (rate-limited, not
    #                                unguarded)
    public_paths: set[str] = Field(
        default={"/login", "/health", "/home", "/", "/apply"},
        validation_alias="OFM_PUBLIC_PATHS",
    )
    public_prefixes: tuple[str, ...] = Field(
        default=("/static/", "/auth/", "/password/", "/events/"),
        validation_alias="OFM_PUBLIC_PREFIXES",
    )
    kie_ai_api_key_admin: str | None = None
    # The landing form's bot trap (routers/apply.py): a submit faster than
    # this after the page was served, or with a token older than the TTL,
    # is a bot's.
    apply_min_fill_s: float = Field(
        default=3.0, ge=0, validation_alias="OFM_APPLY_MIN_FILL_S"
    )
    apply_token_ttl_s: int = Field(
        default=24 * 3600, ge=60, validation_alias="OFM_APPLY_TOKEN_TTL_S"
    )

    # Finished jobs kept per person per task (repositories/jobs.py
    # `_enforce_cap`); queued and running ones are never evicted. Was 500 --
    # prod was full in 9 days, and one 120-link /clone-links Generate makes
    # 120 rows. Not higher: the admin action log reads (and caches) the whole
    # table.
    max_jobs: int = Field(default=5000, ge=1, validation_alias="OFM_JOBS_MAX_ENTRIES")
    recovery_sweep_interval_s: int = Field(
        default=300, validation_alias="OFM_RECOVERY_SWEEP_INTERVAL_S"
    )
    # A running job no worker holds and the queue does not hold is taken as
    # cut off (a deploy killed its worker) once it is at least this old --
    # never one created a moment ago and not enqueued yet.
    recovery_orphan_grace_s: int = Field(
        default=120, ge=0, validation_alias="OFM_RECOVERY_ORPHAN_GRACE_S"
    )
    gallery_limit: int = Field(default=20, validation_alias="OFM_GALLERY_LIMIT")
    # /download-assets cards per page: the first page paints fast, the rest
    # come in on scroll (gallery-scroll.js).
    download_gallery_page: int = Field(
        default=6, ge=1, validation_alias="OFM_DOWNLOAD_GALLERY_PAGE"
    )
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
    # How many "recently picked" files features/assets/ref_usage.py keeps in its Redis
    # sorted set. Only the first recent_used_limit are ever shown; the rest is
    # headroom so a file doesn't drop out the moment it stops being top-5.
    ref_usage_max_tracked: int = Field(
        default=200, validation_alias="OFM_REF_USAGE_MAX_TRACKED"
    )
    # Bounds the ffprobe/ffmpeg call behind a video reference thumbnail.
    ffmpeg_timeout_s: int = Field(default=20, validation_alias="OFM_FFMPEG_TIMEOUT_S")
    # What galleries show instead of a result (features/previews/render.py): a video's
    # height cap, bitrate cap and encode bound, its poster frame's box, and
    # an image's box (2x a card, so the hover zoom stays sharp).
    preview_height: int = Field(
        default=720, ge=144, validation_alias="OFM_PREVIEW_HEIGHT"
    )
    preview_maxrate: str = Field(
        default="1500k", validation_alias="OFM_PREVIEW_MAXRATE"
    )
    preview_timeout_s: int = Field(
        default=180, validation_alias="OFM_PREVIEW_TIMEOUT_S"
    )
    poster_size: int = Field(default=480, ge=16, validation_alias="OFM_POSTER_SIZE")
    image_preview_px: int = Field(
        default=1024, ge=64, validation_alias="OFM_IMAGE_PREVIEW_PX"
    )
    # How long one startup's preview backfill (features/previews/backfill.py) keeps
    # other processes starting at the same time from running it too.
    preview_backfill_guard_s: int = Field(
        default=3600, ge=1, validation_alias="OFM_PREVIEW_BACKFILL_GUARD_S"
    )
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
    # The /clone form's live cost estimate: credits one clone run costs per
    # second of its source video, by resolution. kie.ai's Wan 3.0 is 8 / 16
    # / 32 credits per billed second (480P/720P/1080P; 720P = $0.08/s), billed
    # = output + reference video, so twice that per source second (2026-10-17
    # history: 81 runs at 720P averaged 32.78). JSON in the env var.
    clone_credits_per_video_s: dict[str, float] = Field(
        default={"480P": 16.0, "720P": 32.0, "1080P": 64.0},
        validation_alias="OFM_CLONE_CREDITS_PER_VIDEO_S",
    )
    # What one kie.ai credit costs in dollars: $50 buys 10,000.
    kie_usd_per_credit: float = Field(
        default=0.005, gt=0, validation_alias="OFM_KIE_USD_PER_CREDIT"
    )
    # The same estimate on Higgsfield, measured on prod: the key saved
    # 2026-11-02 19:49 UTC paid $29 for 7 Wan 3.0 runs at 720P with sound,
    # 67 s of source in all = $0.433 per source second (6.93 credits at
    # 16 credits = $1). The 60% discount measured 2026-10-18 ($0.04 a billed
    # second) is gone. 480P/1080P: half/double, as on its price list.
    clone_hf_credits_per_video_s: dict[str, float] = Field(
        default={"480P": 3.46, "720P": 6.93, "1080P": 13.86},
        validation_alias="OFM_CLONE_HF_CREDITS_PER_VIDEO_S",
    )
    # What one Higgsfield credit costs in dollars: 16 credits = $1.
    hf_usd_per_credit: float = Field(
        default=0.0625, gt=0, validation_alias="OFM_HF_USD_PER_CREDIT"
    )
    # The same estimate on RunningHub, in dollars per source second: its
    # Wan 3.0 is $0.07 per billed second at 720P (user's account,
    # 2026-11-02), billed = output + min(reference, 15) s, so twice that per
    # source second. 480P/1080P: half/double, as Alibaba prices them.
    clone_rh_usd_per_video_s: dict[str, float] = Field(
        default={"480P": 0.07, "720P": 0.14, "1080P": 0.28},
        validation_alias="OFM_CLONE_RH_USD_PER_VIDEO_S",
    )
    # The same estimate on Elyum, in dollars per source second: Wan 3.0 is
    # $0.0396 / $0.0792 / $0.1584 per second of clip + output (480p/720p/
    # 1080p, elyum.ai/open/models/alibaba/wan-3.0, 2026-11-02: "the clip's
    # seconds are always charged"), so twice that per source second.
    clone_elyum_usd_per_video_s: dict[str, float] = Field(
        default={"480P": 0.0792, "720P": 0.1584, "1080P": 0.3168},
        validation_alias="OFM_CLONE_ELYUM_USD_PER_VIDEO_S",
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
    # alone, then downloads, and /clone may re-encode it for export (up to
    # exports.timeout_s) -- far past RQ's 180s default. 45 min covers all
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
    # The preview worker's pool (`python -m ofmhelpers.worker previews`): each
    # one is an ffmpeg encode, kept off the generation workers' CPUs.
    preview_workers: int = Field(
        default=1, ge=1, validation_alias="OFM_PREVIEW_WORKERS"
    )
    # The /clone-links pool (`python -m ofmhelpers.worker clone-links`): how
    # many of a Generate's runs download, pay and wait at once. Its own
    # queue, so a 120-link paste never holds the default workers for hours;
    # small, so it also caps concurrent provider submits.
    clone_links_workers: int = Field(
        default=5, ge=1, validation_alias="OFM_CLONE_LINKS_WORKERS"
    )
    # The media pool (`python -m ofmhelpers.worker media`): how many local encodes
    # of big videos run at once. One (see `QueueName.MEDIA`).
    media_workers: int = Field(default=1, ge=1, validation_alias="OFM_MEDIA_WORKERS")

    @property
    def queues(self) -> dict[QueueName, QueueConfig]:
        """Every queue, top priority first: the default one on top, the heavy
        encodes at the bottom."""
        return {
            QueueName.DEFAULT: QueueConfig(
                workers=self.rq_workers, priority=QueuePriority.HIGHEST
            ),
            QueueName.CLONE_LINKS: QueueConfig(
                workers=self.clone_links_workers, priority=QueuePriority.HIGH
            ),
            QueueName.PREVIEWS: QueueConfig(
                workers=self.preview_workers, priority=QueuePriority.LOW
            ),
            QueueName.MEDIA: QueueConfig(
                workers=self.media_workers, priority=QueuePriority.LOWEST
            ),
        }

    # Everything the app writes on behalf of a user lives under one root:
    # uploads/assets (the shared reference store), plus a subdir per tool.
    # Bind-mounted in both compose files, so a container swap keeps it.
    uploads_root: str = Field(default="uploads", validation_alias="OFM_UPLOADS_ROOT")
    # TTL on the repository cache-aside reads, see
    # db/repositories/cached_repository.py. Short: it
    # absorbs the read burst a page render makes, it is not a write-through
    # cache, and every write bumps its namespace anyway.
    cache_ttl_s: int = Field(default=300, validation_alias="OFM_CACHE_TTL_S")
    # Which deployment this is. Only the server's .env says "production";
    # anything else (dev, CI, tests) is kept off live resources that must
    # only ever be touched by production (the live Drive folder).
    environment: Literal["production", "development"] = Field(
        default="development", validation_alias="OFM_ENV"
    )
    # Every outside service (Drive, AI providers, Instagram) swapped for its
    # in-process fake (`ofmhelpers.fakes`). E2E runs with it on; production
    # never does.
    fake_externals: bool = Field(default=False, validation_alias="OFM_FAKE_EXTERNALS")
    # Where fakes keep state shared between processes (the app under test
    # and the test seeding it). Empty = in memory, this process only.
    fakes_dir: str = Field(default="", validation_alias="OFM_FAKES_DIR")

    @property
    def production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def _no_fakes_in_production(self) -> InfraSettings:
        """The fakes sign anyone in as any email (/auth/fake-consent):
        production refuses to start with them."""
        if self.fake_externals and self.production:
            msg = "OFM_FAKE_EXTERNALS=1 is refused with OFM_ENV=production"
            raise ValueError(msg)
        return self


class ProviderSettings(BaseSettings):
    """integrations/ai_providers/: what every provider's group has. A group's env
    vars are its `env_prefix` + the field's name (`OFM_RH_REQUEST_TIMEOUT_S`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # One API call; an upload streams a whole reference video, so this is generous.
    request_timeout_s: float = 300
    # How long an uploaded file's URL or id is remembered; the provider's own
    # retention decides, so each group sets it.
    upload_cache_ttl_s: int
    # Reference images over either limit go up as a shrunk JPEG copy; a bigger one
    # fails after submit (an 8262 px sheet on Higgsfield's Wan 3.0, 2026-10-19; a
    # 422 "file size exceeds limit" on kie.ai). Wan 3.0 documents 20MB / 8000 px per
    # side; bytes are halved for the models whose limit is undocumented.
    ref_image_max_bytes: int = 10 * 1024 * 1024
    ref_image_max_side: int = 8000

    @property
    def ref_image_limits(self) -> ImageLimits:
        return ImageLimits(
            max_bytes=self.ref_image_max_bytes, max_side=self.ref_image_max_side
        )


class KieAISettings(ProviderSettings):
    """integrations/ai_providers/kie/: kie.ai's endpoints, upload limits and audit
    logs. What every provider shares (output dir, polling) is AigenSettings."""

    model_config = SettingsConfigDict(env_prefix="OFM_KIEAI_")

    task_log: str = "/app/kieai_out/tasks.jsonl"
    completions_log: str = "/app/kieai_out/completions.jsonl"
    # Well under kie.ai's own tempfile retention, and every hit is confirmed live
    # before it is trusted anyway, so this only bounds how long a dead entry can
    # keep costing one HEAD.
    upload_cache_ttl_s: int = 12 * 3600

    jobs_base: str = "https://api.kie.ai/api/v1/jobs"
    # A different host from the API: kie.ai serves uploads off its own CDN.
    upload_base: str = "https://kieai.redpandaai.co"

    # HTTP timeouts, split by what the call actually waits on: an upload
    # streams a whole reference file, a download streams a result, and the
    # task-queue calls are small JSON round trips.
    upload_timeout_s: int = 900
    request_timeout_s: float = 30
    # The best-effort HEAD behind the upload cache: it fails closed into a
    # re-upload, so waiting on it is never worth much.
    remote_check_timeout_s: int = 10


class AigenSettings(BaseSettings):
    """integrations/ai_providers/runner.py, integrations/ai_providers/tasks.py, features/jobs/recovery.py:
    what every provider shares -- where results land, how long a poll waits
    before handing the job to recovery, and how long recovery keeps trying.

    The output dir keeps reading OFM_KIEAI_OUT_DIR so results stay where
    kie.ai's always went (the file manager and fake AI model read it too)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The provider every generation page (/generate, /clone) starts on.
    default_provider: str = Field(
        default="higgsfield", validation_alias="OFM_DEFAULT_PROVIDER"
    )
    # The most images one /generate submit may queue (each its own job and card).
    max_batch: int = Field(default=10, ge=1, validation_alias="OFM_AIGEN_MAX_BATCH")
    # How long a run waits for another worker's upload of the same file
    # (base.upload_lock), and how long a held lock lives if its holder dies.
    upload_lock_s: int = Field(default=300, ge=10, validation_alias="OFM_UPLOAD_LOCK_S")
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
    # A result download that fails (reset, stall, CDN 5xx) is tried again
    # after each of these waits (runner.generate): the paid result's URL
    # stays valid for hours, a lost download is a lost generation.
    download_retry_waits_s: list[float] = Field(
        default=[5, 15], validation_alias="OFM_AIGEN_DOWNLOAD_RETRY_WAITS_S"
    )
    # A rate-limited submit (`ProviderRateLimitError`: the provider created
    # nothing) is sent again after each of these waits, +0-25% jitter, or
    # after the provider's own Retry-After; never more than
    # `rate_limit_max_wait_s` in all (well inside OFM_RQ_JOB_TIMEOUT_S, 2700).
    # Any other submit failure is never resent: it may have been paid.
    rate_limit_waits_s: list[float] = Field(
        default=[10, 30, 60], validation_alias="OFM_AIGEN_RATE_LIMIT_WAITS_S"
    )
    rate_limit_max_wait_s: float = Field(
        default=180, ge=0, validation_alias="OFM_AIGEN_RATE_LIMIT_MAX_WAIT_S"
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


class HiggsfieldSettings(ProviderSettings):
    """integrations/ai_providers/higgsfield/. Keys are Higgsfield's `KEY_ID:KEY_SECRET`
    pair as one string, pre-filled per role like the kie.ai keys; unset just
    means the form's key field starts empty."""

    model_config = SettingsConfigDict(env_prefix="OFM_HF_")

    api_key_admin: str | None = Field(default=None, validation_alias="HF_API_KEY_ADMIN")
    base_url: str = "https://api.higgsfield.ai"
    upload_cache_ttl_s: int = 24 * 3600
    # The HEAD behind the upload cache. kie.ai's variable still counts: this
    # check used to read it, and a server's .env may set it.
    remote_check_timeout_s: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "OFM_HF_REMOTE_CHECK_TIMEOUT_S", "OFM_KIEAI_REMOTE_CHECK_TIMEOUT_S"
        ),
    )
    # A reference video that is not MP4 (.mov, .webm) is converted before
    # upload (utils/video_mp4.py). Remuxing H.264 takes seconds. Encoding a
    # 60 s 4K HEVC iPhone clip on the 4 vCPU prod box takes minutes.
    video_convert_timeout_s: int = 900


class RunningHubSettings(ProviderSettings):
    """integrations/ai_providers/runninghub/. The admin's key pre-fills their form
    like the kie.ai and Higgsfield keys; unset = the key field starts
    empty."""

    model_config = SettingsConfigDict(env_prefix="OFM_RH_")

    api_key_admin: str | None = Field(
        default=None, validation_alias="RUNNINGHUB_API_KEY_ADMIN"
    )
    # International site; runninghub.cn is the mainland one (other accounts).
    base_url: str = "https://www.runninghub.ai"
    # Its upload URLs are signed for 24 h: kept for less.
    upload_cache_ttl_s: int = 20 * 3600


class ElyumSettings(ProviderSettings):
    """integrations/ai_providers/elyum/ (elyum.ai public API, `ek_live_...` keys with
    the generate + spend scopes). The admin's key pre-fills their form like
    the other providers' keys; unset = the key field starts empty."""

    model_config = SettingsConfigDict(env_prefix="OFM_ELYUM_")

    api_key_admin: str | None = Field(
        default=None, validation_alias="ELYUM_API_KEY_ADMIN"
    )
    base_url: str = "https://api.elyum.ai"
    # "Upload once and reuse the returned id"; its lifetime is not
    # documented, so it is kept a day at most.
    upload_cache_ttl_s: int = 20 * 3600
    # Its one file limit (POST /v1/files: 60 MB a file), images included.
    ref_image_max_bytes: int = Field(
        default=60 * 1000 * 1000, validation_alias="OFM_ELYUM_MAX_FILE_BYTES"
    )
    # An .mkv/.m4v reference goes up as an MP4 copy (utils/video_mp4.py).
    video_convert_timeout_s: int = 900

    @property
    def max_file_bytes(self) -> int:
        return self.ref_image_max_bytes


class DownloadersSettings(BaseSettings):
    """downloaders/cookies.py, downloaders/generic.py, routers/cookies.py."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The old agency-wide cookies.txt. Read once, by alembic b1c2d3e4f5a7,
    # which moves it into the password admin's own cookies (user_cookies);
    # the app never reads it. The file itself is left in place.
    cookies_file: str = Field(
        default="cookies/cookies.txt", validation_alias="OFM_COOKIES_FILE"
    )
    # One page fetch or one video stream's connect/read wait (downloaders/kvs.py).
    http_timeout_s: int = Field(
        default=60, ge=1, validation_alias="OFM_DOWNLOAD_HTTP_TIMEOUT_S"
    )
    # ffprobe on a downloaded video, to refuse a web page saved as one.
    probe_timeout_s: int = Field(
        default=30, ge=1, validation_alias="OFM_DOWNLOAD_PROBE_TIMEOUT_S"
    )
    # Largest cookies.txt /cookies takes. One site's export is a few KB, but
    # an "all cookies" browser export runs to megabytes (2026-10-23: a real
    # one was refused at the old 1 MB).
    cookies_max_bytes: int = Field(
        default=20 * 1024 * 1024, validation_alias="OFM_COOKIES_MAX_BYTES"
    )
    bgutil_pot_provider_url: str | None = None
    cookies_from_browser: str | None = Field(
        default=None, validation_alias="OFM_COOKIES_FROM_BROWSER"
    )
    # Bounds one gallery-dl run, which may pull a whole Instagram post set.
    image_download_timeout_s: int = Field(
        default=600, validation_alias="OFM_IMAGE_DOWNLOAD_TIMEOUT_S"
    )
    # Bounds one TikTok H.264 re-encode (ffmpeg, CRF 18); past it the
    # original file is kept, as on any ffmpeg failure. Below RQ's job limit.
    reencode_timeout_s: int = Field(
        default=900, ge=1, validation_alias="OFM_DOWNLOAD_REENCODE_TIMEOUT_S"
    )
    # A one-post read or download (`DownloadConfig.single_post`, /clone-links)
    # takes at most this many entries: a carousel's slides, never a whole
    # profile or playlist pasted by mistake.
    single_post_items: int = Field(
        default=10, ge=1, validation_alias="OFM_DOWNLOAD_SINGLE_POST_ITEMS"
    )


class InstagramSettings(BaseSettings):
    """instagram/client.py, routers/downloads/reels.py, features/reels/watch.py."""

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
    # /reels feed: cards per page (the rest load on scroll). 24 = a few
    # screens: a page of full-size covers was megabytes (prod 2026-10-22).
    feed_page_size: int = Field(
        default=24, ge=1, validation_alias="OFM_IG_FEED_PAGE_SIZE"
    )
    # Width of the kept feed thumbnails (features/reels/thumbs.py), px.
    feed_thumb_px: int = Field(
        default=270, ge=64, validation_alias="OFM_IG_FEED_THUMB_PX"
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
    # max_duration_s never is (the feed also hides stored reels past it, so a
    # lower cap applies before the next rescan). 15 s is the most Wan 3.0
    # (what /clone runs) takes, on every provider.
    min_views: int = Field(default=50_000, ge=0, validation_alias="OFM_IG_MIN_VIEWS")
    max_duration_s: float = Field(
        default=15.0, gt=0, validation_alias="OFM_IG_MAX_DURATION_S"
    )
    # features/reels/watch.py: whether watched profiles are rescanned at all (off
    # since 2026-10-23, /reels retired to More: only a just-added profile's
    # first scan runs), how stale a scan may get, how often the sweeper
    # looks, and how long a claimed scan may run before another sweep takes
    # it over.
    rescan_enabled: bool = Field(
        default=False, validation_alias="OFM_IG_RESCAN_ENABLED"
    )
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
    # /accounts: newest reels read per tracked account (reels tab, page_size
    # a request) -- the range its view windows can cover.
    track_max_reels: int = Field(
        default=96, ge=1, validation_alias="OFM_IG_TRACK_MAX_REELS"
    )
    # /accounts: list reels through the uploaded login (cookies.txt) when it
    # works -- the only view with an account's sensitive/age-restricted
    # reels; off, or without a working login, the logged-out tab is used.
    track_logged_in: bool = Field(
        default=True, validation_alias="OFM_IG_TRACK_LOGGED_IN"
    )


class AccountsSettings(BaseSettings):
    """features/accounts/tracker.py, routers/accounts.py: tracked social accounts
    (every platform of `ofmhelpers.integrations.socials`). Each platform's own knobs
    (proxies, how many posts a scan reads) live in its own group."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # How stale an account's numbers may get, how often the sweeper looks,
    # and how long a claimed scan may sit queued + run before another sweep
    # takes it over.
    rescan_interval_s: int = Field(
        default=6 * 60 * 60, ge=60, validation_alias="OFM_ACCOUNTS_RESCAN_INTERVAL_S"
    )
    # An account whose scans keep failing (banned, deleted) is retried at
    # twice the interval per consecutive failure, up to this: fewer requests
    # for dead accounts, so the proxies stay unflagged for the live ones.
    max_rescan_interval_s: int = Field(
        default=24 * 60 * 60,
        ge=60,
        validation_alias="OFM_ACCOUNTS_MAX_RESCAN_INTERVAL_S",
    )
    rescan_check_interval_s: int = Field(
        default=300, ge=10, validation_alias="OFM_ACCOUNTS_RESCAN_CHECK_INTERVAL_S"
    )
    claim_ttl_s: int = Field(
        default=2 * 60 * 60, ge=60, validation_alias="OFM_ACCOUNTS_CLAIM_TTL_S"
    )
    # features/accounts/tracker.py: pin the periodic sweep to once a day, not
    # before this local time plus a per-day random delay -- a fixed schedule
    # is what gets an Instagram login flagged as automation. None: the
    # interval alone decides, whenever an account falls due.
    rescan_daily_at: str | None = Field(
        default=None, validation_alias="OFM_ACCOUNTS_RESCAN_DAILY_AT"
    )
    # How far past `rescan_daily_at` the day's run may land.
    rescan_daily_jitter_s: int = Field(
        default=600, ge=0, validation_alias="OFM_ACCOUNTS_RESCAN_DAILY_JITTER_S"
    )

    @field_validator("rescan_daily_at")
    @classmethod
    def _daily_at(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            hour, minute = (int(part) for part in value.split(":"))
            datetime.time(hour, minute)  # ValueError outside 0..23 / 0..59
        except ValueError:
            raise ValueError(_DAILY_AT_MSG) from None
        return f"{hour:02d}:{minute:02d}"

    # Accounts per worker job: jobs run in parallel on the worker pool, each
    # rotating through the platform's proxies from its own starting point.
    batch_size: int = Field(
        default=20, ge=1, validation_alias="OFM_ACCOUNTS_BATCH_SIZE"
    )
    # Other proxies one account is retried on, and a random pause up to
    # retry_jitter_s before each retry.
    proxy_retries: int = Field(
        default=3, ge=0, validation_alias="OFM_ACCOUNTS_PROXY_RETRIES"
    )
    retry_jitter_s: float = Field(
        default=2.0, ge=0, validation_alias="OFM_ACCOUNTS_RETRY_JITTER_S"
    )
    # The reporting periods of the period selector, in days, in selector
    # order (1 = the last 24 hours): views of posts published in the period
    # (vs the period before it) and follower growth against the newest
    # snapshot that old. The default must be one of them.
    periods_days: list[int] = Field(
        default=[1, 7, 14, 30],
        min_length=1,
        validation_alias="OFM_ACCOUNTS_PERIODS_DAYS",
    )
    default_period_days: int = Field(
        default=7, ge=1, validation_alias="OFM_ACCOUNTS_DEFAULT_PERIOD_DAYS"
    )
    # Analytics page: how far back the history goes, and how many of the
    # latest synced posts it ranks when the period has none.
    history_days: int = Field(
        default=90, ge=1, validation_alias="OFM_ACCOUNTS_HISTORY_DAYS"
    )
    top_posts: int = Field(default=10, ge=1, validation_alias="OFM_ACCOUNTS_TOP_POSTS")
    page_sizes: list[int] = Field(
        default=[25, 50, 100, 200],
        min_length=1,
        validation_alias="OFM_ACCOUNTS_PAGE_SIZES",
    )
    default_page_size: int = Field(
        default=50, ge=1, validation_alias="OFM_ACCOUNTS_DEFAULT_PAGE_SIZE"
    )
    # Handles one "Add accounts" paste may hold.
    max_add: int = Field(default=500, ge=1, validation_alias="OFM_ACCOUNTS_MAX_ADD")

    @property
    def post_retention_days(self) -> int:
        """How long a tracked account's posts are kept: twice the longest
        period, so every period compares against the one before it."""
        return 2 * max(self.periods_days)

    @model_validator(mode="after")
    def _default_period_is_offered(self) -> AccountsSettings:
        if self.default_period_days not in self.periods_days:
            msg = (
                f"OFM_ACCOUNTS_DEFAULT_PERIOD_DAYS={self.default_period_days} "
                f"is not in OFM_ACCOUNTS_PERIODS_DAYS={self.periods_days}"
            )
            raise ValueError(msg)
        return self


class SignInSettings(BaseSettings):
    """ofmhelpers/signin, routers/auth.py, stores/users.py: how people sign
    in -- Google, Apple, or their own email + password -- and who is an
    admin no matter what /users says."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Comma-separated: these sign in as admins even when no /users row has
    # them yet (the way in on a fresh install, and back in if every admin
    # was removed).
    admin_emails_raw: str = Field(default="", validation_alias="OFM_ADMIN_EMAILS")

    # Google: an OAuth "Web application" client of the Cloud project the
    # Drive bot lives in. Redirect URI `<APP_BASE_URL>auth/google/callback`.
    # Its sign-in also grants Drive, which uploads go up as.
    google_client_id: str = Field(default="", validation_alias="OFM_GOOGLE_CLIENT_ID")
    google_client_secret: SecretStr | None = Field(
        default=None, validation_alias="OFM_GOOGLE_CLIENT_SECRET"
    )

    # Sign in with Apple (Apple Developer account): the Services ID, the
    # team, and a Sign in with Apple key (.p8 PEM; "\n" for line breaks
    # in .env). Return URL `<APP_BASE_URL>auth/apple/callback`.
    apple_services_id: str = Field(default="", validation_alias="OFM_APPLE_SERVICES_ID")
    apple_team_id: str = Field(default="", validation_alias="OFM_APPLE_TEAM_ID")
    apple_key_id: str = Field(default="", validation_alias="OFM_APPLE_KEY_ID")
    apple_private_key: SecretStr | None = Field(
        default=None, validation_alias="OFM_APPLE_PRIVATE_KEY"
    )

    # Email + password: the shortest password accepted, and how long a
    # set-password link (made on /users) stays good.
    password_min_length: int = Field(
        default=10, ge=8, validation_alias="OFM_PASSWORD_MIN_LENGTH"
    )
    password_link_ttl_s: int = Field(
        default=7 * 86400, ge=600, validation_alias="OFM_PASSWORD_LINK_TTL_S"
    )
    # Password hashes derived at once per process (each ~128 MiB of
    # scrypt memory): caps what a burst of /login POSTs can allocate.
    password_hash_concurrency: int = Field(
        default=4, ge=1, validation_alias="OFM_PASSWORD_HASH_CONCURRENCY"
    )
    # How long a Google/Apple consent may take before its state is refused.
    state_ttl_s: int = Field(
        default=600, ge=60, validation_alias="OFM_SIGNIN_STATE_TTL_S"
    )

    @property
    def admin_emails(self) -> tuple[str, ...]:
        """In the order given: the first one takes over the password
        account (and the agency's data) at their first Google sign-in."""
        found = (p.strip().lower() for p in self.admin_emails_raw.split(","))
        return tuple(dict.fromkeys(e for e in found if e))

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def apple_configured(self) -> bool:
        return bool(
            self.apple_services_id
            and self.apple_team_id
            and self.apple_key_id
            and self.apple_private_key
        )


class TelemetrySettings(BaseSettings):
    """ofmhelpers/telemetry.py: traces, logs and metrics to Pydantic Logfire.
    No token: nothing is sent (tests, CI, a fresh checkout)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # A project write token (Logfire: Settings -> Write tokens). Secret.
    token: SecretStr | None = Field(default=None, validation_alias="LOGFIRE_TOKEN")
    # What is running (a git sha on deploy); tells releases apart.
    release: str = Field(default="dev", validation_alias="OFM_RELEASE")
    # The most a work horse waits for its spans to go out before it exits.
    flush_timeout_s: float = Field(
        default=5.0, gt=0, validation_alias="OFM_TELEMETRY_FLUSH_S"
    )

    @field_validator("token", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        """`LOGFIRE_TOKEN=` (blank) is off, like no token at all."""
        return None if isinstance(value, str) and not value.strip() else value


class LinksSettings(BaseSettings):
    """ofmhelpers/links, features/links/sync.py, routers/tracking/links.py: short
    links on link providers (Oopsie, Bouncy). A user's own key (/account)
    wins; an admin with none runs on the `*_API_KEY_ADMIN` one."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    oopsie_api_key_admin: str | None = Field(
        default=None, validation_alias="OOPSIE_API_KEY_ADMIN"
    )
    bouncy_api_key_admin: str | None = Field(
        default=None, validation_alias="BOUNCY_API_KEY_ADMIN"
    )
    request_timeout_s: float = Field(
        default=30, gt=0, validation_alias="OFM_LINKS_REQUEST_TIMEOUT_S"
    )
    # How stale a user's links and their numbers may get, how often the
    # sweeper looks, and how long a claimed sync may run before another
    # sweep takes it over.
    sync_interval_s: int = Field(
        default=3 * 60 * 60, ge=60, validation_alias="OFM_LINKS_SYNC_INTERVAL_S"
    )
    sync_check_interval_s: int = Field(
        default=300, ge=10, validation_alias="OFM_LINKS_SYNC_CHECK_INTERVAL_S"
    )
    claim_ttl_s: int = Field(
        default=60 * 60, ge=60, validation_alias="OFM_LINKS_CLAIM_TTL_S"
    )
    # A link still "creating" this long after it was asked for lost its job
    # (a deploy): failed, unless the provider lists it by then.
    create_ttl_s: int = Field(
        default=30 * 60, ge=60, validation_alias="OFM_LINKS_CREATE_TTL_S"
    )
    # Period selector of /links, in days, in selector order (1 = today, UTC);
    # each sync reads the daily numbers of the longest one.
    periods_days: list[int] = Field(
        default=[1, 7, 30, 90], min_length=1, validation_alias="OFM_LINKS_PERIODS_DAYS"
    )
    default_period_days: int = Field(
        default=7, ge=1, validation_alias="OFM_LINKS_DEFAULT_PERIOD_DAYS"
    )
    page_sizes: list[int] = Field(
        default=[25, 50, 100, 200],
        min_length=1,
        validation_alias="OFM_LINKS_PAGE_SIZES",
    )
    default_page_size: int = Field(
        default=50, ge=1, validation_alias="OFM_LINKS_DEFAULT_PAGE_SIZE"
    )
    # Links one "Generate links" may ask for (one per Instagram account).
    max_generate: int = Field(
        default=100, ge=1, validation_alias="OFM_LINKS_MAX_GENERATE"
    )

    @property
    def history_days(self) -> int:
        """How many days back a sync reads: the longest period, and also
        what /accounts' periods need."""
        return max(*self.periods_days, 30)

    def admin_key(self, provider: str) -> str | None:
        return getattr(self, f"{provider}_api_key_admin", None) or None

    @model_validator(mode="after")
    def _default_period_is_offered(self) -> LinksSettings:
        if self.default_period_days not in self.periods_days:
            msg = (
                f"OFM_LINKS_DEFAULT_PERIOD_DAYS={self.default_period_days} "
                f"is not in OFM_LINKS_PERIODS_DAYS={self.periods_days}"
            )
            raise ValueError(msg)
        return self


class VaultSettings(BaseSettings):
    """db/encrypted.py: the Fernet keys the /phones logins and backup
    codes are encrypted with at rest. Comma-separated, newest first: the
    first key encrypts, every key decrypts (rotate by prepending a new one,
    drop the old one once nothing uses it). Losing every key loses the
    logins -- keep a copy off the server."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    keys: SecretStr | None = Field(default=None, validation_alias="OFM_VAULT_KEY")

    def key_list(self) -> list[str]:
        if self.keys is None:
            return []
        return [k.strip() for k in self.keys.get_secret_value().split(",") if k.strip()]


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


class ExportSettings(BaseSettings):
    """How a finished video is exported (`ofmhelpers.media.exports`): the default
    choice, the bound on one export's encode, and the device exports' phone
    time zone (IANA, so DST follows the date) and place -- each video lands
    at a random point within `jitter_m` of `center`, reporting a GPS
    accuracy from `accuracy_m` (2.0-2.7 m, the real iPhone 17 samples'
    range). Default Midtown Manhattan; JSON in the env var, `null` = no
    location (Location Services off)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    default: str = Field(default="capcut", validation_alias="OFM_EXPORT_DEFAULT")
    # Where the pages' Mirror switch starts.
    mirror: bool = Field(default=False, validation_alias="OFM_EXPORT_MIRROR")
    # Tries of one video's export before its run fails: the provider's raw
    # file is never delivered instead (user rule 2026-10-25). A run whose
    # export failed every try keeps its paid request: recovery runs it again
    # (resumed, never paid twice) until `reruns` runs failed so.
    attempts: int = Field(default=3, ge=1, validation_alias="OFM_EXPORT_ATTEMPTS")
    reruns: int = Field(default=3, ge=1, validation_alias="OFM_EXPORT_RERUNS")
    timeout_s: int = Field(
        default=600,
        validation_alias=AliasChoices(
            "OFM_EXPORT_TIMEOUT_S", "OFM_CLONE_CAPCUT_TIMEOUT_S"
        ),
    )
    device_timezone: str = Field(
        default="America/New_York", validation_alias="OFM_EXPORT_DEVICE_TIMEZONE"
    )
    device_place: Place | None = Field(
        default=Place(
            center=GeoPoint(latitude=40.7549, longitude=-73.9840, altitude_m=10.0),
            jitter_m=1500,
            accuracy_m=(2.0, 2.7),
        ),
        validation_alias="OFM_EXPORT_DEVICE_PLACE",
    )


class CloneLongSettings(BaseSettings):
    """/clone-long (`features/clone_long`): one image + one long video through
    Wan 3.0 in pieces of at most 15 s."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The longest video taken: a mistaken hour-long upload must not spend
    # for 240 pieces.
    max_video_s: int = Field(default=600, ge=1, validation_alias="OFM_CLONE_LONG_MAX_S")
    # Pieces of one video generating at the same time: 5 makes 75 s one round.
    parallel_pieces: int = Field(
        default=5, ge=1, validation_alias="OFM_CLONE_LONG_PARALLEL"
    )
    # One cut or the join (re-encodes: minutes for a long 1080p video).
    ffmpeg_timeout_s: int = Field(
        default=900, ge=1, validation_alias="OFM_CLONE_LONG_FFMPEG_TIMEOUT_S"
    )
    # A cut piece's short side at most (Wan 3.0 makes 1080P at most): a 4K
    # phone video's pieces re-encoded at full size would be uploaded at
    # several times the original's bitrate, once per piece.
    piece_max_side: int = Field(
        default=1080, ge=2, validation_alias="OFM_CLONE_LONG_PIECE_MAX_SIDE"
    )
    # Colour-match every piece to the first before the join (each piece is a
    # separate generation, so they drift in exposure/tint; the seams show it).
    colour_match: bool = Field(
        default=False, validation_alias="OFM_CLONE_LONG_COLOUR_MATCH"
    )
    # Generate pieces in order, each from the previous piece's last frame
    # (passed as a second reference image, prompt "Start video from @Image2"),
    # so the subject carries across the seam. Off = independent pieces,
    # generated `parallel_pieces` at a time (faster, but seams jump).
    chain: bool = Field(default=False, validation_alias="OFM_CLONE_LONG_CHAIN")
    # Ask the provider to generate audio per piece and keep it through the
    # join. Off = silent (the long clone had no sound).
    audio: bool = Field(default=False, validation_alias="OFM_CLONE_LONG_AUDIO")


class LLMSettings(BaseSettings):
    """`ofmhelpers.integrations.llm`: language models that read text, images and video
    and answer in a typed shape, behind one interface. One default per kind
    of work (`llm.LLMUse`), each `<provider>` or `<provider>:<model>` (the
    provider's own model field when no model is named). Each provider's key
    and model are its own fields here (a new provider adds its pair)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Work that reads video (a AI check): Gemini watches the real video.
    # 3.6-flash first (user decision 2026-10-25): on dev that day 3.8-flash
    # answered 503 overloaded for 10+ minutes while 3.6-flash answered both
    # checks in seconds.
    default_video: str = Field(
        default="gemini:gemini-3.6-flash",
        min_length=1,
        validation_alias="LLM_DEFAULT_VIDEO",
    )
    # Who a AI check asks next, in order, when the one before is down,
    # rate limited, out of quota or not set up (comma-separated
    # `<provider>[:<model>]`). Gemini's free quota is per model and per
    # project (dev and prod share one key): 20 requests a day for each of
    # 3.8/3.7/3.6/3.5-flash, all used up on 2026-10-25; 2.5-flash is closed
    # to new keys. Measured on dev the same day, 7 reels (one a man in a
    # strapless tube top, six clean, checked frame by frame at 4K):
    # 3.5-flash-lite 7/7 in 2-6 s; Groq's qwen3.8-27b GOOD on all 7 (its one
    # image model, so Groq never checks a reel); 3-flash-preview GLITCHY on
    # all 7 ("fused fingers" that are not there).
    fallback_video: str = Field(
        default=(
            "gemini:gemini-3.8-flash,gemini:gemini-3.5-flash,"
            "gemini:gemini-3.5-flash-lite"
        ),
        validation_alias="LLM_FALLBACK_VIDEO",
    )
    # Text (and image) work: Groq, fast and not kept by default.
    default_chat: str = Field(
        default="groq", min_length=1, validation_alias="LLM_DEFAULT_CHAT"
    )

    @property
    def gemini_keys(self) -> list[str]:
        """Every Gemini key in `GEMINI_API_KEY`, in order, each once."""
        raw = self.gemini_api_key.get_secret_value() if self.gemini_api_key else ""
        keys = (k.strip() for k in raw.replace("\n", ",").split(","))
        return list(dict.fromkeys(k for k in keys if k))

    @property
    def video_chain(self) -> list[str]:
        """The default video provider, then each fallback once."""
        names = [self.default_video, *self.fallback_video.split(",")]
        return list(dict.fromkeys(n.strip() for n in names if n.strip()))

    # Google AI Studio (https://aistudio.google.com/apikey): free tier, no
    # card. Unpaid-tier terms (ai.google.dev/gemini-api/terms, 2026-04-28):
    # inputs improve Google's products and human reviewers may read them;
    # no sensitive, confidential or personal information. The user accepted
    # that for /clone-links checks on 2026-10-25 (llm/CLAUDE.md).
    # One key or several, comma-separated (`gemini_keys`): each project has
    # its own free daily quota, and the AI check tries the keys in order on
    # every model before the next model (user ask 2026-10-25).
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias="GEMINI_API_KEY"
    )
    # A stable id, free on the free tier (ai.google.dev pricing, 2026-10-24).
    gemini_model: str = Field(
        default="gemini-3.8-flash", min_length=1, validation_alias="OFM_GEMINI_MODEL"
    )
    # GroqCloud (https://console.groq.com/keys): free plan, no card. Inference
    # data is not kept by default (logs up to 30 days; Zero Data Retention is
    # a switch in Data Controls).
    groq_api_key: SecretStr | None = Field(
        default=None, validation_alias="GROQ_API_KEY"
    )
    # Groq's vision model (console.groq.com/docs/vision, 2026-10-24): images
    # only, at most 3 per request; free plan 30/min, 1,000/day, 8K tokens/min.
    groq_model: str = Field(
        default="qwen/qwen3.8-27b", min_length=1, validation_alias="OFM_GROQ_MODEL"
    )
    # Cerebras (https://cloud.cerebras.ai): Free Trial tier. Its vision model
    # (inference-docs.cerebras.ai, 2026-10-24): at most 2 images and 10 MiB
    # per request; free 5 requests/min, 1M tokens/day.
    cerebras_api_key: SecretStr | None = Field(
        default=None, validation_alias="CEREBRAS_API_KEY"
    )
    cerebras_model: str = Field(
        default="qwen-3.8-27b", min_length=1, validation_alias="OFM_CEREBRAS_MODEL"
    )
    # Images go to a chat-completions provider (Groq, Cerebras) shrunk to
    # this: tokens per image grow with its size, and free plans count tokens
    # per minute (Groq: 8K).
    image_max_side: int = Field(
        default=1024, ge=64, validation_alias="OFM_LLM_IMAGE_MAX_SIDE"
    )
    image_max_bytes: int = Field(
        default=3 * 1024 * 1024, ge=1024, validation_alias="OFM_LLM_IMAGE_MAX_BYTES"
    )
    # A provider that reads images but not video gets a video as one contact
    # sheet: this many stills, evenly spaced, tiled in time order, each this
    # wide.
    video_sheet_frames: int = Field(
        default=9, ge=1, le=36, validation_alias="OFM_LLM_VIDEO_SHEET_FRAMES"
    )
    video_sheet_frame_px: int = Field(
        default=360, ge=64, validation_alias="OFM_LLM_VIDEO_SHEET_FRAME_PX"
    )
    video_sheet_timeout_s: int = Field(
        default=60, ge=1, validation_alias="OFM_LLM_VIDEO_SHEET_TIMEOUT_S"
    )
    # One request, media upload included. gemini-3.8-flash took 60 and 87 s
    # to answer one AI check (a 9-still sheet) on the free tier (dev,
    # 2026-10-25): 120 s left too little room before a retry.
    timeout_s: float = Field(default=300, gt=0, validation_alias="OFM_LLM_TIMEOUT_S")
    # Rate limited / overloaded / 5xx: tries after the first, waiting the
    # provider's own retry hint, else `retry_wait_s` doubling, at most
    # `retry_max_wait_s` each. A hint longer than that (a daily quota used
    # up) is not waited for: the call fails at once.
    retries: int = Field(default=4, ge=0, validation_alias="OFM_LLM_RETRIES")
    # An answer in the wrong shape (JSON mode missed): asked again this many
    # times before it counts as the answer (llm/base.py `_reasked`).
    shape_retries: int = Field(
        default=1, ge=0, validation_alias="OFM_LLM_SHAPE_RETRIES"
    )
    retry_wait_s: float = Field(
        default=5, ge=0, validation_alias="OFM_LLM_RETRY_WAIT_S"
    )
    retry_max_wait_s: float = Field(
        default=60, ge=0, validation_alias="OFM_LLM_RETRY_MAX_WAIT_S"
    )
    # A media file up to this size goes inside the request; a bigger one is
    # uploaded first (Gemini: 20 MB per request, base64 adds a third).
    inline_max_bytes: int = Field(
        default=14 * 1024 * 1024, ge=0, validation_alias="OFM_LLM_INLINE_MAX_BYTES"
    )
    # An uploaded file must be processed before a request may use it.
    file_ready_timeout_s: float = Field(
        default=120, gt=0, validation_alias="OFM_LLM_FILE_READY_TIMEOUT_S"
    )


class CloneAiCheckSettings(BaseSettings):
    """`ofmhelpers.features.clone_links.ai_check`: is a generated reel too glitchy to post? A
    contact sheet of the whole video goes to the video model
    (`LLM_DEFAULT_VIDEO`), which answers GOOD or GLITCHY."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # One still per this many seconds of video, first and last frame
    # included, at most `max_frames` (a 15 s reel: 16 stills, 4 x 4).
    every_s: float = Field(
        default=1.0, gt=0, validation_alias="OFM_CLONE_AI_CHECK_EVERY_S"
    )
    max_frames: int = Field(
        default=16, ge=2, le=36, validation_alias="OFM_CLONE_AI_CHECK_MAX_FRAMES"
    )
    # Each still's width: a face must stay readable on the sheet.
    frame_px: int = Field(
        default=360, ge=64, validation_alias="OFM_CLONE_AI_CHECK_FRAME_PX"
    )
    # Reading the video and cutting each still.
    ffmpeg_timeout_s: int = Field(
        default=60, ge=1, validation_alias="OFM_CLONE_AI_CHECK_FFMPEG_TIMEOUT_S"
    )
    # A whole check (sheet, every model of the chain, retries) asks no model
    # and waits no retry after this long: the reel goes to TRIAL REELS
    # unchecked. Kept below `OFM_CLONE_LINKS_REVIEW_TIMEOUT_S` (1200, RQ's
    # limit for check + upload) with room for one request in flight
    # (`OFM_LLM_TIMEOUT_S`, 300) and the upload: 4 models x 5 tries of 300 s
    # each came to ~6300 s with no cap (review 2026-10-25).
    deadline_s: float = Field(
        default=600, gt=0, validation_alias="OFM_CLONE_AI_CHECK_DEADLINE_S"
    )
    # GLITCHY when any glitch is blocking or the model is at least this sure
    # (its `glitch_percent`) the reel should not be posted: several minor
    # glitches can add up, and an unsure reel goes where a person looks first.
    glitchy_percent: int = Field(
        default=50, ge=1, le=100, validation_alias="OFM_CLONE_AI_CHECK_GLITCHY_PERCENT"
    )


class CloneLinksSettings(BaseSettings):
    """/clone-links (`features/clone_links`): reel links + a subject image -> one
    Wan 3.0 run per link, each result checked for glitches
    (`ofmhelpers.features.clone_links.ai_check`) and uploaded into the clicker's Drive root: a
    GOOD one into `good_folder`, anything else into `trial_folder`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Links one paste takes: each is previewed (a yt-dlp read through the
    # Instagram proxies, `preview_parallel` at once on the server), then a
    # paid run. A VA's real list was 112 (2026-10-25, user decision: 120).
    max_links: int = Field(default=120, ge=1, validation_alias="OFM_CLONE_LINKS_MAX")
    # How long a link's yt-dlp read (length, cover URL) stays in Redis: a
    # post's video never changes, so only memory bounds it.
    info_ttl_s: int = Field(
        default=30 * 24 * 3600, ge=60, validation_alias="OFM_CLONE_LINKS_INFO_TTL_S"
    )
    # The page warns (and every failed upload says so) when the clicker's
    # Google Drive has less than this free: a clone is ~20-50 MB, and a full
    # Drive refused every upload on the first dev paid run (2026-10-25).
    drive_min_free_mb: int = Field(
        default=500, ge=0, validation_alias="OFM_CLONE_LINKS_DRIVE_MIN_FREE_MB"
    )
    # How long a Drive's free space is trusted before it is asked again.
    drive_space_ttl_s: int = Field(
        default=300, ge=10, validation_alias="OFM_CLONE_LINKS_DRIVE_SPACE_TTL_S"
    )
    # Links Generate's preview reads at once, across every request of the
    # process (yt-dlp, each a page or API request through the Instagram
    # proxies; cached ones cost nothing). No burstier than the 10-process
    # worker download that follows (review 2026-10-25: 16 fired 16 x 5
    # Instagram reads per paste).
    preview_parallel: int = Field(
        default=6, ge=1, le=64, validation_alias="OFM_CLONE_LINKS_PREVIEW_PARALLEL"
    )
    # A whole preview request ends by then: every link not read yet gets a
    # "took too long" tile, so one stalled proxy never keeps Generate grey.
    preview_deadline_s: int = Field(
        default=90, ge=5, validation_alias="OFM_CLONE_LINKS_PREVIEW_DEADLINE_S"
    )
    # yt-dlp's socket timeout for a preview read (its default is 20 s per
    # stalled request, and a logged-out Instagram try makes up to 4).
    preview_socket_timeout_s: float = Field(
        default=10, gt=0, validation_alias="OFM_CLONE_LINKS_PREVIEW_SOCKET_TIMEOUT_S"
    )
    # yt-dlp extractors (`IE_NAME`) whose links are one post: Check takes
    # only these, never a profile, playlist, channel, story tray or a
    # redirector that chains into the Generic extractor.
    video_extractors: list[str] = Field(
        default=[
            "Instagram",
            "TikTok",
            "vm.tiktok",
            "youtube",
            "twitter",
            "facebook",
            "facebook:reel",
        ],
        validation_alias="OFM_CLONE_LINKS_VIDEO_EXTRACTORS",
    )
    # What one cloned reel takes in Drive: the confirm dialog warns when the
    # ticked runs do not fit (CapCut 4K at 28 Mb/s for 15 s is ~53 MB).
    reel_mb: int = Field(default=55, ge=1, validation_alias="OFM_CLONE_LINKS_REEL_MB")
    # After a refusal of the account itself (wrong key, no credits), the
    # rest of that Generate fails at once instead of downloading first.
    halt_ttl_s: int = Field(
        default=3600, ge=60, validation_alias="OFM_CLONE_LINKS_HALT_TTL_S"
    )
    # Folders inside the clicker's root (`drive_folders.root_name()`), found
    # by name or made on first use.
    good_folder: str = Field(
        default="GOOD REELS",
        min_length=1,
        validation_alias="OFM_CLONE_LINKS_GOOD_FOLDER",
    )
    trial_folder: str = Field(
        default="TRIAL REELS",
        min_length=1,
        validation_alias="OFM_CLONE_LINKS_TRIAL_FOLDER",
    )
    # Query parameters a shared link carries that never change what it
    # points at (share trackers): dropped by Check. "utm_*" = every name
    # starting "utm_".
    tracking_params: list[str] = Field(
        default=[
            "igsh",
            "igshid",
            "utm_*",
            "si",
            "fbclid",
            "gclid",
            "_t",
            "_r",
            "is_from_webapp",
            "sender_device",
            "share_app_id",
            "share_link_id",
            "share_item_id",
            "social_sharing",
        ],
        validation_alias="OFM_CLONE_LINKS_TRACKING_PARAMS",
    )
    # RQ's time limit for one review (check + upload): above the AI check's
    # whole deadline (`CloneAiCheckSettings.deadline_s`) plus the upload.
    review_timeout_s: int = Field(
        default=1200, ge=60, validation_alias="OFM_CLONE_LINKS_REVIEW_TIMEOUT_S"
    )
    # A review claimed by a worker (`started_at`) and still running after
    # this long was cut off (a deploy, OOM or crash killed it): recovery
    # queues it again. Longer than `review_timeout_s`, so a live one is
    # never doubled. A review not yet claimed is waiting, never cut off.
    review_stale_s: int = Field(
        default=1800, ge=60, validation_alias="OFM_CLONE_LINKS_REVIEW_STALE_S"
    )
    # A review no worker claimed for this long has probably lost its queued
    # job: recovery queues it again. Longer than the worst queue seen.
    review_lost_s: int = Field(
        default=6 * 3600, ge=60, validation_alias="OFM_CLONE_LINKS_REVIEW_LOST_S"
    )
    # How many times one review is started (first run + recovery's
    # re-queues of a cut-off one or a failed upload) before it stays failed.
    review_attempts: int = Field(
        default=3, ge=1, validation_alias="OFM_CLONE_LINKS_REVIEW_ATTEMPTS"
    )


class GDriveSettings(BaseSettings):
    """/drive (`ofmhelpers.integrations.gdrive`, features/drive/folders.py): each phone
    owner's own Google Drive, through their Google sign-in (`SignInSettings`
    google_*): an "ALL ACCOUNTS" folder ("DEV - ALL ACCOUNTS" outside
    production) with a "<handle> - <owner>" folder per Instagram login and a
    `posted_folder_name` folder inside each, which people move a video into
    once it is posted. No folder id is configured anywhere.

    `service_account` + `root_folder_id`: the live Drive contract tests'
    sandbox, and on prod the agency admin's fallback: until the password
    account has a Google token, /drive reads and makes its folders through
    that account in that root (prod's "ALL ACCOUNTS", shared with it;
    features/drive/folders.py `_bot_root`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_account: SecretStr | None = Field(
        default=None, validation_alias="OFM_GDRIVE_SERVICE_ACCOUNT"
    )
    root_folder_id: str = Field(default="", validation_alias="OFM_GDRIVE_ROOT_FOLDER")
    # Dev only (ignored in production): read the live "ALL ACCOUNTS" folders
    # to debug prod's Drive state, instead of the "DEV - ALL ACCOUNTS"
    # sandbox. Every write to Drive is blocked while it is on
    # (gdrive.client.ReadOnlyDrive), and the sweep does not run, so the live
    # folders can only be read, never changed.
    read_live: bool = Field(default=False, validation_alias="OFM_DRIVE_READ_LIVE")
    # The folder inside each account folder for posted videos. Matched
    # case-insensitively, so one made by hand ("Posted") is adopted.
    posted_folder_name: str = Field(
        default="POSTED", min_length=1, validation_alias="OFM_GDRIVE_POSTED_FOLDER"
    )
    # IANA: the zone /drive's times are shown in.
    timezone: str = Field(
        default="Europe/Warsaw", validation_alias="OFM_GDRIVE_TIMEZONE"
    )
    # When the team posts, in UTC (fixed all year: no daylight saving shift).
    # Posting runs from `posting_window_before_h` before it to
    # `posting_window_after_h` after it; a posting day -- what "Posted today"
    # counts -- starts at the window's start (2026-10-22: 19:45 UTC, so
    # 18:45-00:45 UTC, every day).
    posting_time_utc: datetime.time = Field(
        default=datetime.time(19, 45), validation_alias="OFM_GDRIVE_POSTING_TIME_UTC"
    )
    posting_window_before_h: float = Field(
        default=1, ge=0, le=23, validation_alias="OFM_GDRIVE_POSTING_BEFORE_H"
    )
    posting_window_after_h: float = Field(
        default=5, ge=0, le=23, validation_alias="OFM_GDRIVE_POSTING_AFTER_H"
    )
    # How often the sweeper runs: makes missing folders, and lists every
    # POSTED folder to record what was posted (a few list calls per account).
    check_interval_s: int = Field(
        default=300, ge=30, validation_alias="OFM_GDRIVE_CHECK_INTERVAL_S"
    )
    # Per request: googleapiclient's backoff on 5xx/429/dropped connections.
    retries: int = Field(default=5, ge=0, validation_alias="OFM_GDRIVE_RETRIES")
    # When a page waits on Drive (/drive, its previews): each socket wait
    # and the retries, so a stalled Drive fails the page in seconds.
    interactive_timeout_s: float = Field(
        default=10, gt=0, validation_alias="OFM_GDRIVE_INTERACTIVE_TIMEOUT_S"
    )
    interactive_retries: int = Field(
        default=1, ge=0, validation_alias="OFM_GDRIVE_INTERACTIVE_RETRIES"
    )
    # Folders listed at once when /drive opens.
    list_workers: int = Field(
        default=8, ge=1, validation_alias="OFM_GDRIVE_LIST_WORKERS"
    )
    # Longest side of a /drive preview, px.
    thumb_px: int = Field(default=400, ge=32, validation_alias="OFM_GDRIVE_THUMB_PX")
    # A /clone upload still `queued` this long after it was clicked is
    # stranded (worker killed by a deploy, its job lost): Upload queues it
    # again. Longer than a full batch takes, so a live one is never doubled.
    upload_stale_s: int = Field(
        default=3600, ge=60, validation_alias="OFM_GDRIVE_UPLOAD_STALE_S"
    )
    # A person's Drive space Google did not answer for (down, slow): not
    # asked again for this long, so a failing Drive costs one call per
    # person per minute, not one per page view (features/drive/space.py).
    space_retry_s: int = Field(
        default=60, ge=5, validation_alias="OFM_GDRIVE_SPACE_RETRY_S"
    )
    # /clone-links reviews of one person find or make their root and
    # GOOD/TRIAL REELS one at a time (a Redis lock): ten parallel reviews
    # made ten folders of one name. A review waits at most this long, then
    # goes ahead without it (features/drive/folders.py `review_folder`).
    review_lock_s: int = Field(
        default=60, ge=1, validation_alias="OFM_GDRIVE_REVIEW_LOCK_S"
    )
    # How long a found review folder's id is remembered (checked with one
    # get before each use; Drive's name search can lag a fresh folder).
    review_folder_ttl_s: int = Field(
        default=7 * 24 * 3600, ge=60, validation_alias="OFM_GDRIVE_REVIEW_FOLDER_TTL_S"
    )


class RepurposeSettings(BaseSettings):
    """`ofmhelpers.media.repurpose`: the checker's optional detectors and the knob
    lab. All off unless set: the app image has neither torch nor the TMK
    tools; the lab pod sets them (repurpose/CLAUDE.md, Lab)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SSCD (checker/sscd.py): the TorchScript model file; unset = not used.
    sscd_model: str | None = Field(default=None, validation_alias="OFM_SSCD_MODEL")
    # Cosine at or above which two frames are the same picture.
    sscd_threshold: float = Field(
        default=0.75, gt=0, le=1, validation_alias="OFM_SSCD_THRESHOLD"
    )
    # torch threads per process (the lab runs ~20 processes on 32 vCPUs).
    sscd_threads: int = Field(default=2, ge=1, validation_alias="OFM_SSCD_THREADS")
    # TMK+PDQF (checker/tmk.py): folder of the built ThreatExchange tools.
    tmk_dir: str | None = Field(default=None, validation_alias="OFM_TMK_DIR")
    # The lab's VMAF (lab/measure.py): an ffmpeg with libvmaf (Debian's has
    # none); unset = SSIM x 100 instead.
    lab_vmaf_ffmpeg: str | None = Field(
        default=None, validation_alias="OFM_LAB_VMAF_FFMPEG"
    )
    # The snapchat caption's font file (extras/snapchat); unset = the first
    # installed of Arial / Liberation Sans / DejaVu Sans.
    snapchat_font: str | None = Field(
        default=None, validation_alias="OFM_SNAPCHAT_FONT"
    )


class TrialsSettings(BaseSettings):
    """/trials (`ofmhelpers.features.trials`): how long its jobs may take. How
    many run at once is the media queue's (`InfraSettings.media_workers`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Seconds per reel a job may take (pipeline encode + the LLM, an upload),
    # even made one after another. Prod 2026-10-30 (4 vCPU, 2 at once): 21 4K
    # copies in 110 min, ~10.5 min each; 240 s (dev's pace) killed Generate at
    # 21 of 26.
    per_reel_s: int = Field(default=900, ge=1, validation_alias="OFM_TRIALS_PER_REEL_S")
    job_grace_s: int = Field(
        default=300, ge=0, validation_alias="OFM_TRIALS_JOB_GRACE_S"
    )


class ViralSettings(BaseSettings):
    """/viral (`ofmhelpers.features.viral`): one Instagram profile's most played
    reels, read through an Apify actor (`ofmhelpers.integrations.apify`), the top ones
    downloaded."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Comma-separated Apify API tokens; a search runs on the one with the most
    # credit left. The name the server's .env has held since the lead hunt.
    # A plain string, split by `api_keys`: a list field is parsed as JSON.
    apify_api_keys: str = Field(default="", validation_alias="APIFY_API_KEYS")
    actor: str = Field(
        default="apify~instagram-reel-scraper", validation_alias="OFM_VIRAL_ACTOR"
    )
    # How many of the profile's newest reels a search reads (paid per reel):
    # the top ones are picked from these.
    scan: int = Field(default=200, ge=1, le=5000, validation_alias="OFM_VIRAL_SCAN")
    top: int = Field(default=30, ge=1, le=200, validation_alias="OFM_VIRAL_TOP")
    # The actor's Free-plan prices (apify.com/apify/instagram-reel-scraper/
    # pricing): only for the page's "up to $X a search"; what a search cost
    # is read from its run.
    usd_per_reel: float = Field(
        default=0.0026, ge=0, validation_alias="OFM_VIRAL_USD_PER_REEL"
    )
    usd_per_run: float = Field(
        default=0.001, ge=0, validation_alias="OFM_VIRAL_USD_PER_RUN"
    )
    # One run's limit on Apify (it stops the run then).
    run_timeout_s: int = Field(
        default=900, ge=60, validation_alias="OFM_VIRAL_RUN_TIMEOUT_S"
    )
    download_workers: int = Field(
        default=6, ge=1, le=32, validation_alias="OFM_VIRAL_DOWNLOAD_WORKERS"
    )
    download_timeout_s: float = Field(
        default=60, gt=0, validation_alias="OFM_VIRAL_DOWNLOAD_TIMEOUT_S"
    )

    @property
    def api_keys(self) -> list[str]:
        return [k.strip() for k in self.apify_api_keys.split(",") if k.strip()]

    @property
    def max_usd(self) -> float:
        """The most one search costs: every scanned reel charged."""
        return self.scan * self.usd_per_reel + self.usd_per_run


class ChattingSettings(BaseSettings):
    """/chatting and its pop-ups, whichever platform (`features/chatting/`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # A fan waiting longer than this with no reply = "Chats unanswered".
    unanswered_after_s: int = Field(
        default=300, ge=1, validation_alias="OFM_CHATTING_UNANSWERED_AFTER_S"
    )
    # The period the average covers, and how far back a chat's first read goes. A fan
    # still waiting counts however long ago they wrote.
    lookback_s: int = Field(
        default=86400, ge=1, validation_alias="OFM_CHATTING_LOOKBACK_S"
    )
    # How often the sync is asked if it is due.
    check_interval_s: float = Field(
        default=60, gt=0, validation_alias="OFM_CHATTING_CHECK_INTERVAL_S"
    )
    # How often pages ask for the pop-ups: a chatter's reply should show at once.
    alerts_check_s: float = Field(
        default=10, gt=0, validation_alias="OFM_CHATTING_ALERTS_CHECK_S"
    )
    # This server's public address for webhooks. Unset: APP_BASE_URL when it is public
    # HTTPS, else no webhooks and the sync does the work.
    webhook_base_url: str = Field(
        default="", validation_alias="OFM_CHATTING_WEBHOOK_BASE_URL"
    )
    # How often the sync reads everything once webhooks deliver messages; also how
    # often a model's destination is checked.
    webhook_sync_interval_s: float = Field(
        default=3600, gt=0, validation_alias="OFM_CHATTING_WEBHOOK_SYNC_INTERVAL_S"
    )
    # A model with no delivery for this long counts as not delivering, and is read
    # every minute: all a dead webhook can cost.
    webhook_quiet_s: int = Field(
        default=600, ge=1, validation_alias="OFM_CHATTING_WEBHOOK_QUIET_S"
    )
    # How old a signed delivery may be: an older one is a recording played back.
    webhook_tolerance_s: int = Field(
        default=300, ge=1, validation_alias="OFM_CHATTING_WEBHOOK_TOLERANCE_S"
    )
    # Far above a real delivery, far below what could hurt the server.
    webhook_max_body_bytes: int = Field(
        default=1_048_576, ge=1, validation_alias="OFM_CHATTING_WEBHOOK_MAX_BODY_BYTES"
    )
    # Deliveries failing the signature from one address before it is turned away.
    webhook_max_refused: int = Field(
        default=30, ge=1, validation_alias="OFM_CHATTING_WEBHOOK_MAX_REFUSED"
    )
    webhook_refused_window_s: int = Field(
        default=300, ge=1, validation_alias="OFM_CHATTING_WEBHOOK_REFUSED_WINDOW_S"
    )
    # A sale above this many cents, before fees, is worth a pop-up.
    sale_notify_cents: int = Field(
        default=5000, ge=0, validation_alias="OFM_CHATTING_SALE_NOTIFY_CENTS"
    )
    # How long a mass message, a new subscriber or a big sale can show, unless closed.
    notification_shown_s: int = Field(
        default=86400, ge=1, validation_alias="OFM_CHATTING_NOTIFICATION_SHOWN_S"
    )
    # How many creators /chatting offers as recently checked.
    recent_models_shown: int = Field(
        default=3, ge=1, validation_alias="OFM_CHATTING_RECENT_MODELS"
    )
    # This many chats in a row that cannot be read end a sync round: the platform is
    # down, and each one costs its retries.
    sync_max_failed_chats: int = Field(
        default=5, ge=1, validation_alias="OFM_CHATTING_SYNC_MAX_FAILED_CHATS"
    )


class FanvueSettings(BaseSettings):
    """Fanvue's API (`ofmhelpers.integrations.fanvue`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    client_id: str = Field(default="", validation_alias="FANVUE_CLIENT_ID")
    client_secret: str = Field(
        default="", repr=False, validation_alias="FANVUE_CLIENT_SECRET"
    )
    # The app's one signing secret, from the Events tab of Fanvue's Developer Area.
    event_secret: str = Field(
        default="", repr=False, validation_alias="FANVUE_EVENT_SECRET"
    )
    api_url: str = Field(
        default="https://api.fanvue.com/v1", validation_alias="OFM_FANVUE_API_URL"
    )
    authorize_url: str = Field(
        default="https://auth.fanvue.com/oauth2/auth",
        validation_alias="OFM_FANVUE_AUTHORIZE_URL",
    )
    token_url: str = Field(
        default="https://auth.fanvue.com/oauth2/token",
        validation_alias="OFM_FANVUE_TOKEN_URL",
    )
    api_version: str = Field(
        default="2025-06-26", validation_alias="OFM_FANVUE_API_VERSION"
    )
    # Each must also be enabled on the app in Fanvue's Developer Area.
    scopes: str = Field(
        default="openid offline_access offline read:self read:chat read:creator",
        validation_alias="OFM_FANVUE_SCOPES",
    )
    timeout_s: float = Field(default=30, gt=0, validation_alias="OFM_FANVUE_TIMEOUT_S")
    # 50 is the API's largest page.
    page_size: int = Field(
        default=50, ge=1, le=50, validation_alias="OFM_FANVUE_PAGE_SIZE"
    )
    tries: int = Field(default=4, ge=1, validation_alias="OFM_FANVUE_TRIES")
    retry_wait_s: float = Field(
        default=2.0, ge=0, validation_alias="OFM_FANVUE_RETRY_WAIT_S"
    )
    max_wait_s: float = Field(
        default=65.0, ge=0, validation_alias="OFM_FANVUE_MAX_WAIT_S"
    )
    # A token this close to expiring is refreshed before it is used.
    token_refresh_margin_s: int = Field(
        default=120, ge=0, validation_alias="OFM_FANVUE_TOKEN_REFRESH_MARGIN_S"
    )
    # A refresh whose answer got lost is presented again at once, this many tries.
    refresh_tries: int = Field(
        default=3, ge=1, validation_alias="OFM_FANVUE_REFRESH_TRIES"
    )
    # How long someone has to finish connecting in their browser.
    connect_ttl_s: int = Field(
        default=600, ge=1, validation_alias="OFM_FANVUE_CONNECT_TTL_S"
    )


class StackedSettings(BaseSettings):
    """Stacked's API (`ofmhelpers.integrations.stacked`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_url: str = Field(
        default="https://api.stacked.com/external-service/v1",
        validation_alias="OFM_STACKED_API_URL",
    )
    timeout_s: float = Field(default=30, gt=0, validation_alias="OFM_STACKED_TIMEOUT_S")
    # 100 is the API's largest page.
    page_size: int = Field(
        default=100, ge=1, le=100, validation_alias="OFM_STACKED_PAGE_SIZE"
    )
    # Reads are safe to retry, a 429 after its Retry-After (API docs, "Retries").
    tries: int = Field(default=4, ge=1, validation_alias="OFM_STACKED_TRIES")
    retry_wait_s: float = Field(
        default=2.0, ge=0, validation_alias="OFM_STACKED_RETRY_WAIT_S"
    )
    # Rate limits are one-minute windows, so no real wait is longer.
    max_wait_s: float = Field(
        default=65.0, ge=0, validation_alias="OFM_STACKED_MAX_WAIT_S"
    )

    @property
    def api_keys(self) -> dict[str, str]:
        """Each `STACKED_API_KEY_<NAME>` as {name: key}, sorted. A new creator
        or agency = a new line in .env; the API says which kind a key is."""
        found = {
            **DotEnvSettingsSource(type(self)).env_vars,
            **EnvSettingsSource(type(self)).env_vars,
        }
        return {
            name.removeprefix(_STACKED_KEY_PREFIX): key
            for name, key in sorted(found.items())
            if name.startswith(_STACKED_KEY_PREFIX) and key
        }


class PicturesSettings(BaseSettings):
    """/pictures: presets (a prompt + reference images) run through Nano
    Banana Pro with each picture to recreate."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # A preset's reference images; a run also sends the picture to recreate,
    # and the model caps the total (kie.ai 8, RunningHub 10).
    preset_max_images: int = Field(
        default=10, ge=1, le=20, validation_alias="OFM_PICTURES_PRESET_MAX_IMAGES"
    )
    # Pictures one submit may recreate (one run each).
    max_pictures: int = Field(
        default=20, ge=1, le=100, validation_alias="OFM_PICTURES_MAX_PICTURES"
    )
    # Output size, in the model's own spelling ("2K" / "2k"); user rule
    # 2026-11-01: 2K output, best-quality input (references go up untouched
    # under the model's own limit, `ModelSpec.max_upload_bytes`).
    resolution: str = Field(default="2K", validation_alias="OFM_PICTURES_RESOLUTION")
    # Every run's shape unless its preset fixes another; "" = the picture's
    # own (user rule 2026-11-01: all pictures 3:4).
    aspect_ratio: str = Field(
        default="3:4", validation_alias="OFM_PICTURES_ASPECT_RATIO"
    )
