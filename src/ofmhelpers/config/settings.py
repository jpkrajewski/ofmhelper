# Copyright (C) 2026 jpkrajewski

import datetime as dt
import os
from enum import IntEnum, StrEnum
from types import MappingProxyType
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

from ofmhelpers.config.types import (
    AccountFolder,
    GeoPoint,
    ImageLimits,
    Place,
    StackedKey,
)

_SCAN_TIMES_MSG = "OFM_ACCOUNTS_SCAN_TIMES must be HH:MM, comma-separated"
_STACKED_KEY_PREFIX = "stacked_api_key_"


class QueueName(StrEnum):
    DEFAULT = "default"
    EVENTS = "events"
    EDIT = "edit"
    CLONE_LINKS = "clone-links"
    PREVIEWS = "previews"
    MEDIA = "media"


JOB_QUEUES = (QueueName.DEFAULT, QueueName.CLONE_LINKS, QueueName.MEDIA, QueueName.EDIT)


class QueuePriority(IntEnum):
    HIGHEST = 0
    HIGH = 5
    LOW = 15
    LOWEST = 19


class QueueConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    workers: int
    priority: QueuePriority


SESSION_SECRET_MIN_LENGTH = 32


class SessionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    session_secret: str
    session_https_only: bool = False
    session_max_age_s: int = 60 * 60 * 5
    environment: str = Field(default="development", validation_alias="OFM_ENV")

    @model_validator(mode="after")
    def _a_real_secret_in_production(self) -> SessionSettings:
        short = len(self.session_secret) < SESSION_SECRET_MIN_LENGTH
        if self.environment == "production" and short:
            msg = (
                f"SESSION_SECRET is shorter than {SESSION_SECRET_MIN_LENGTH}"
                " characters: refused with OFM_ENV=production"
                " (openssl rand -hex 32)"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _https_cookies_in_production(self) -> SessionSettings:
        if self.environment == "production" and not self.session_https_only:
            msg = "SESSION_HTTPS_ONLY=true is required with OFM_ENV=production"
            raise ValueError(msg)
        return self


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_password_admin: str | None = Field(default=None, repr=False)
    base_url: str = Field(default="", validation_alias="APP_BASE_URL")

    public_paths: set[str] = Field(
        default={
            "/login",
            "/login/va",
            "/health",
            "/",
            "/apply",
            "/privacy",
            "/terms",
        },
        validation_alias="OFM_PUBLIC_PATHS",
    )
    contact_email: str = Field(default="", validation_alias="OFM_CONTACT_EMAIL")
    public_prefixes: tuple[str, ...] = Field(
        default=("/static/", "/auth/", "/password/", "/events/", "/files/ai/"),
        validation_alias="OFM_PUBLIC_PREFIXES",
    )
    kie_ai_api_key_admin: str | None = Field(default=None, repr=False)
    apply_min_fill_s: float = Field(
        default=3.0,
        ge=0,
        validation_alias="OFM_APPLY_MIN_FILL_S",
    )
    apply_token_ttl_s: int = Field(
        default=24 * 3600,
        ge=60,
        validation_alias="OFM_APPLY_TOKEN_TTL_S",
    )

    max_jobs: int = Field(default=5000, ge=1, validation_alias="OFM_JOBS_MAX_ENTRIES")
    recovery_sweep_interval_s: int = Field(
        default=300,
        validation_alias="OFM_RECOVERY_SWEEP_INTERVAL_S",
    )
    recovery_orphan_grace_s: int = Field(
        default=120,
        ge=0,
        validation_alias="OFM_RECOVERY_ORPHAN_GRACE_S",
    )
    recovery_restarts: bool | None = Field(
        default=None,
        validation_alias="OFM_RECOVERY_RESTARTS",
    )
    gallery_limit: int = Field(default=20, validation_alias="OFM_GALLERY_LIMIT")
    download_gallery_page: int = Field(
        default=6,
        ge=1,
        validation_alias="OFM_DOWNLOAD_GALLERY_PAGE",
    )
    gallery_live_poll_s: int = Field(
        default=5,
        ge=1,
        validation_alias="OFM_GALLERY_LIVE_POLL_S",
    )
    va_session_max_age_s: int = Field(
        default=60 * 60 * 2,
        ge=60,
        validation_alias="OFM_VA_SESSION_MAX_AGE_S",
    )
    seen_write_every_s: int = Field(
        default=60,
        ge=1,
        validation_alias="OFM_SEEN_WRITE_EVERY_S",
    )
    va_idle_sweep_s: int = Field(
        default=60,
        ge=5,
        validation_alias="OFM_VA_IDLE_SWEEP_S",
    )
    va_reels_notice_every: int = Field(
        default=10,
        ge=1,
        validation_alias="OFM_VA_REELS_NOTICE_EVERY",
    )
    va_spend_notice_every_usd: int = Field(
        default=10,
        ge=1,
        validation_alias="OFM_VA_SPEND_NOTICE_EVERY_USD",
    )
    no_credits_notice_every_s: int = Field(
        default=3600,
        ge=1,
        validation_alias="OFM_NO_CREDITS_NOTICE_EVERY_S",
    )
    handmade_max_upload_mb: int = Field(
        default=1024,
        ge=1,
        validation_alias="OFM_HANDMADE_MAX_UPLOAD_MB",
    )
    handmade_max_files: int = Field(
        default=20,
        ge=1,
        validation_alias="OFM_HANDMADE_MAX_FILES",
    )
    image_download_prefix: str = Field(
        default="IMG_",
        validation_alias="OFM_IMAGE_DOWNLOAD_PREFIX",
    )
    video_download_prefix: str = Field(
        default="VID_",
        validation_alias="OFM_VIDEO_DOWNLOAD_PREFIX",
    )
    download_name_digits: int = Field(
        default=4,
        ge=1,
        validation_alias="OFM_DOWNLOAD_NAME_DIGITS",
    )
    character_sheet_max_images: int = Field(
        default=10,
        validation_alias="OFM_CHARACTER_SHEET_MAX_IMAGES",
    )

    rate_limit_enabled: bool = Field(
        default=True,
        validation_alias="OFM_RATE_LIMIT_ENABLED",
    )
    login_max_failures: int = Field(
        default=10,
        validation_alias="OFM_LOGIN_MAX_FAILURES",
    )
    login_failure_window_s: int = Field(
        default=900,
        validation_alias="OFM_LOGIN_FAILURE_WINDOW_S",
    )
    write_rate_limit_requests: int = Field(
        default=120,
        validation_alias="OFM_WRITE_RATE_LIMIT_REQUESTS",
    )
    write_rate_limit_window_s: int = Field(
        default=60,
        validation_alias="OFM_WRITE_RATE_LIMIT_WINDOW_S",
    )

    recent_used_limit: int = Field(
        default=5,
        validation_alias="OFM_REFS_RECENT_USED_LIMIT",
    )
    recent_upload_limit: int = Field(
        default=5,
        validation_alias="OFM_REFS_RECENT_UPLOAD_LIMIT",
    )
    max_ref_limit: int = Field(default=60, validation_alias="OFM_REFS_MAX_LIMIT")
    ref_usage_max_tracked: int = Field(
        default=200,
        validation_alias="OFM_REF_USAGE_MAX_TRACKED",
    )
    ffmpeg_timeout_s: int = Field(default=20, validation_alias="OFM_FFMPEG_TIMEOUT_S")
    preview_height: int = Field(
        default=720,
        ge=144,
        validation_alias="OFM_PREVIEW_HEIGHT",
    )
    preview_maxrate: str = Field(
        default="1500k",
        validation_alias="OFM_PREVIEW_MAXRATE",
    )
    preview_timeout_s: int = Field(
        default=180,
        validation_alias="OFM_PREVIEW_TIMEOUT_S",
    )
    poster_size: int = Field(default=480, ge=16, validation_alias="OFM_POSTER_SIZE")
    image_preview_px: int = Field(
        default=1024,
        ge=64,
        validation_alias="OFM_IMAGE_PREVIEW_PX",
    )
    preview_backfill_guard_s: int = Field(
        default=3600,
        ge=1,
        validation_alias="OFM_PREVIEW_BACKFILL_GUARD_S",
    )
    uniqify_max_copies: int = Field(
        default=50,
        validation_alias="OFM_UNIQIFY_MAX_COPIES",
    )
    uniqify_max_trim_s: float = Field(
        default=5.0,
        gt=0,
        validation_alias="OFM_UNIQIFY_MAX_TRIM_S",
    )
    uniqify_encode_timeout_s: int = Field(
        default=600,
        validation_alias="OFM_UNIQIFY_ENCODE_TIMEOUT_S",
    )
    edit_cut_timeout_s: int = Field(
        default=300,
        validation_alias="OFM_EDIT_CUT_TIMEOUT_S",
    )
    edit_min_s: float = Field(default=0.5, gt=0, validation_alias="OFM_EDIT_MIN_S")
    edit_max_part_s: float = Field(
        default=60,
        gt=0,
        validation_alias="OFM_EDIT_MAX_PART_S",
    )
    edit_max_clips: int = Field(default=10, ge=1, validation_alias="OFM_EDIT_MAX_CLIPS")
    edit_max_input_s: float = Field(
        default=600,
        gt=0,
        validation_alias="OFM_EDIT_MAX_INPUT_S",
    )
    edit_max_upload_mb: int = Field(
        default=1024,
        ge=1,
        validation_alias="OFM_EDIT_MAX_UPLOAD_MB",
    )
    edit_threads: int = Field(default=2, ge=1, validation_alias="OFM_EDIT_THREADS")
    edit_strip_timeout_s: int = Field(
        default=30,
        ge=1,
        validation_alias="OFM_EDIT_STRIP_TIMEOUT_S",
    )
    edit_strip_all_frames_s: float = Field(
        default=60,
        ge=0,
        validation_alias="OFM_EDIT_STRIP_ALL_FRAMES_S",
    )
    edit_keep_s: int = Field(default=86400, ge=60, validation_alias="OFM_EDIT_KEEP_S")
    edit_sweep_s: int = Field(default=3600, ge=60, validation_alias="OFM_EDIT_SWEEP_S")
    edit_strip_frames: int = Field(
        default=20,
        ge=1,
        le=60,
        validation_alias="OFM_EDIT_STRIP_FRAMES",
    )
    edit_strip_height: int = Field(
        default=96,
        ge=16,
        le=360,
        validation_alias="OFM_EDIT_STRIP_HEIGHT",
    )
    edit_proxy_short_side: int = Field(
        default=540,
        ge=144,
        validation_alias="OFM_EDIT_PROXY_SHORT_SIDE",
    )
    edit_proxy_gop: int = Field(
        default=12,
        ge=1,
        le=300,
        validation_alias="OFM_EDIT_PROXY_GOP",
    )
    edit_proxy_crf: int = Field(
        default=30,
        ge=0,
        le=51,
        validation_alias="OFM_EDIT_PROXY_CRF",
    )
    edit_prepare_timeout_s: int = Field(
        default=600,
        ge=10,
        validation_alias="OFM_EDIT_PREPARE_TIMEOUT_S",
    )
    edit_frame_timeout_s: int = Field(
        default=30,
        ge=1,
        validation_alias="OFM_EDIT_FRAME_TIMEOUT_S",
    )
    edit_frame_parallel: int = Field(
        default=2,
        ge=1,
        validation_alias="OFM_EDIT_FRAME_PARALLEL",
    )
    edit_frame_wait_s: float = Field(
        default=2,
        ge=0,
        validation_alias="OFM_EDIT_FRAME_WAIT_S",
    )
    clone_max_videos: int = Field(default=20, validation_alias="OFM_CLONE_MAX_VIDEOS")
    clone_max_images: int = Field(
        default=10,
        ge=1,
        le=10,
        validation_alias="OFM_CLONE_MAX_IMAGES",
    )
    clone_last_subjects: int = Field(
        default=5,
        ge=0,
        validation_alias="OFM_CLONE_LAST_SUBJECTS",
    )
    clone_top_subjects: int = Field(
        default=5,
        ge=0,
        validation_alias="OFM_CLONE_TOP_SUBJECTS",
    )
    clone_default_prompt: str = Field(
        default="Replace the subject in @Video1 with subject from @Image1",
        validation_alias="OFM_CLONE_DEFAULT_PROMPT",
    )
    clone_probe_workers: int = Field(
        default=8,
        ge=1,
        validation_alias="OFM_CLONE_PROBE_WORKERS",
    )
    clone_credits_per_video_s: dict[str, float] = Field(
        default={"480P": 16.0, "720P": 32.0, "1080P": 64.0},
        validation_alias="OFM_CLONE_CREDITS_PER_VIDEO_S",
    )
    kie_usd_per_credit: float = Field(
        default=0.005,
        gt=0,
        validation_alias="OFM_KIE_USD_PER_CREDIT",
    )
    clone_hf_credits_per_video_s: dict[str, float] = Field(
        default={"480P": 3.46, "720P": 6.93, "1080P": 13.86},
        validation_alias="OFM_CLONE_HF_CREDITS_PER_VIDEO_S",
    )
    hf_usd_per_credit: float = Field(
        default=0.0625,
        gt=0,
        validation_alias="OFM_HF_USD_PER_CREDIT",
    )
    clone_rh_usd_per_video_s: dict[str, float] = Field(
        default={"480P": 0.07, "720P": 0.14, "1080P": 0.28},
        validation_alias="OFM_CLONE_RH_USD_PER_VIDEO_S",
    )
    clone_elyum_usd_per_video_s: dict[str, float] = Field(
        default={"480P": 0.0792, "720P": 0.1584, "1080P": 0.3168},
        validation_alias="OFM_CLONE_ELYUM_USD_PER_VIDEO_S",
    )
    clone_apiyi_usd_per_video_s: dict[str, float] = Field(
        default={"480P": 0.0588, "720P": 0.1176, "1080P": 0.2352},
        validation_alias="OFM_CLONE_APIYI_USD_PER_VIDEO_S",
    )
    clone_fake_usd_per_video_s: dict[str, float] = Field(
        default={"480P": 0.005, "720P": 0.01, "1080P": 0.02},
        validation_alias="OFM_CLONE_FAKE_USD_PER_VIDEO_S",
    )

    def restarting(self, production: bool) -> bool:  # noqa: FBT001
        return production if self.recovery_restarts is None else self.recovery_restarts


def _compose_database_url() -> str:
    password = os.environ.get("POSTGRES_PASSWORD") or "ofmhelpers"
    return f"postgresql+psycopg://ofmhelpers:{password}@postgres:5432/ofmhelpers"


class InfraSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default_factory=_compose_database_url,
        validation_alias="OFM_DATABASE_URL",
    )
    redis_url: str = Field(
        default="redis://redis:6379/0",
        validation_alias="OFM_REDIS_URL",
    )
    rq_job_timeout_s: int = Field(default=2700, validation_alias="OFM_RQ_JOB_TIMEOUT_S")
    rq_async: bool = Field(default=True, validation_alias="OFM_RQ_ASYNC")
    rq_workers: int = Field(default=10, validation_alias="OFM_RQ_WORKERS")
    preview_workers: int = Field(
        default=1,
        ge=1,
        validation_alias="OFM_PREVIEW_WORKERS",
    )
    clone_links_workers: int = Field(
        default=5,
        ge=1,
        validation_alias="OFM_CLONE_LINKS_WORKERS",
    )
    media_workers: int = Field(default=1, ge=1, validation_alias="OFM_MEDIA_WORKERS")
    events_workers: int = Field(default=1, ge=1, validation_alias="OFM_EVENTS_WORKERS")
    edit_workers: int = Field(default=1, ge=1, validation_alias="OFM_EDIT_WORKERS")

    @property
    def queues(self) -> MappingProxyType[str, QueueConfig]:
        entries: dict[str, QueueConfig] = {
            QueueName.DEFAULT: QueueConfig(
                workers=self.rq_workers,
                priority=QueuePriority.HIGHEST,
            ),
            QueueName.EVENTS: QueueConfig(
                workers=self.events_workers,
                priority=QueuePriority.HIGHEST,
            ),
            QueueName.EDIT: QueueConfig(
                workers=self.edit_workers,
                priority=QueuePriority.HIGHEST,
            ),
            QueueName.CLONE_LINKS: QueueConfig(
                workers=self.clone_links_workers,
                priority=QueuePriority.HIGH,
            ),
            QueueName.PREVIEWS: QueueConfig(
                workers=self.preview_workers,
                priority=QueuePriority.LOW,
            ),
            QueueName.MEDIA: QueueConfig(
                workers=self.media_workers,
                priority=QueuePriority.LOWEST,
            ),
        }
        return MappingProxyType(entries)

    uploads_root: str = Field(default="uploads", validation_alias="OFM_UPLOADS_ROOT")
    cache_ttl_s: int = Field(default=300, validation_alias="OFM_CACHE_TTL_S")
    environment: Literal["production", "development"] = Field(
        validation_alias="OFM_ENV",
    )
    fake_externals: bool = Field(default=False, validation_alias="OFM_FAKE_EXTERNALS")
    fakes_dir: str = Field(default="", validation_alias="OFM_FAKES_DIR")
    debug: bool = Field(default=False, validation_alias="OFM_DEBUG")

    @property
    def production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def _no_debug_in_production(self) -> InfraSettings:
        if self.debug and self.production:
            msg = "OFM_DEBUG=1 is refused with OFM_ENV=production"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _no_fakes_in_production(self) -> InfraSettings:
        if self.fake_externals and self.production:
            msg = "OFM_FAKE_EXTERNALS=1 is refused with OFM_ENV=production"
            raise ValueError(msg)
        return self


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    request_timeout_s: float = 300
    upload_cache_ttl_s: int
    ref_image_max_bytes: int = 10 * 1024 * 1024
    ref_image_max_side: int = 8000

    @property
    def ref_image_limits(self) -> ImageLimits:
        return ImageLimits(
            max_bytes=self.ref_image_max_bytes,
            max_side=self.ref_image_max_side,
        )


class KieAISettings(ProviderSettings):
    model_config = SettingsConfigDict(env_prefix="OFM_KIEAI_")

    upload_cache_ttl_s: int = 12 * 3600

    jobs_base: str = "https://api.kie.ai/api/v1/jobs"
    upload_base: str = "https://kieai.redpandaai.co"

    upload_timeout_s: int = 900
    request_timeout_s: float = 30
    create_timeout_s: float = 180
    webhook_hmac_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "KIE_AI_WEBHOOK_HMAC_KEY",
            "OFM_KIEAI_WEBHOOK_HMAC_KEY",
        ),
    )
    callback_tolerance_s: int = 300
    remote_check_timeout_s: int = 10


class AigenSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    default_provider: str = Field(
        default="higgsfield",
        validation_alias="OFM_DEFAULT_PROVIDER",
    )
    max_batch: int = Field(default=10, ge=1, validation_alias="OFM_AIGEN_MAX_BATCH")
    upload_lock_s: int = Field(default=300, ge=10, validation_alias="OFM_UPLOAD_LOCK_S")
    out_dir: str = Field(
        default="/app/kieai_out",
        validation_alias=AliasChoices("OFM_AIGEN_OUT_DIR", "OFM_KIEAI_OUT_DIR"),
    )
    poll_timeout_s: int = Field(
        default=1000,
        validation_alias=AliasChoices(
            "OFM_AIGEN_POLL_TIMEOUT_S",
            "OFM_KIEAI_POLL_TIMEOUT_S",
        ),
    )
    video_poll_timeout_s: int = Field(
        default=1800,
        validation_alias=AliasChoices(
            "OFM_AIGEN_VIDEO_POLL_TIMEOUT_S",
            "OFM_KIEAI_VIDEO_POLL_TIMEOUT_S",
        ),
    )
    poll_interval_s: float = Field(
        default=2.5,
        validation_alias="OFM_AIGEN_POLL_INTERVAL_S",
    )
    poll_max_interval_s: float = Field(
        default=20.0,
        validation_alias="OFM_AIGEN_POLL_MAX_INTERVAL_S",
    )
    download_timeout_s: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "OFM_AIGEN_DOWNLOAD_TIMEOUT_S",
            "OFM_KIEAI_DOWNLOAD_TIMEOUT_S",
        ),
    )
    download_retry_waits_s: list[float] = Field(
        default=[5, 15],
        validation_alias="OFM_AIGEN_DOWNLOAD_RETRY_WAITS_S",
    )
    rate_limit_waits_s: list[float] = Field(
        default=[10, 30, 60],
        validation_alias="OFM_AIGEN_RATE_LIMIT_WAITS_S",
    )
    rate_limit_max_wait_s: float = Field(
        default=180,
        ge=0,
        validation_alias="OFM_AIGEN_RATE_LIMIT_MAX_WAIT_S",
    )
    unfetched_retry_waits_s: list[float] = Field(
        default=[5, 5, 5],
        validation_alias="OFM_AIGEN_UNFETCHED_RETRY_WAITS_S",
    )
    resume_max_age_s: int = Field(
        default=48 * 3600,
        validation_alias=AliasChoices(
            "OFM_AIGEN_RESUME_MAX_AGE_S",
            "OFM_KIEAI_RESUME_MAX_AGE_S",
        ),
    )
    raw_keep_s: int = Field(
        default=14 * 86400,
        ge=3600,
        validation_alias="OFM_AIGEN_RAW_KEEP_S",
    )
    raw_sweep_s: int = Field(
        default=86400,
        ge=60,
        validation_alias="OFM_AIGEN_RAW_SWEEP_S",
    )
    hosted_ttl_s: int = Field(
        default=3 * 3600,
        ge=3600,
        validation_alias="OFM_AIGEN_HOSTED_TTL_S",
    )
    public_base_url: str | None = Field(
        default=None,
        validation_alias="OFM_AIGEN_PUBLIC_BASE_URL",
    )


class HiggsfieldSettings(ProviderSettings):
    model_config = SettingsConfigDict(env_prefix="OFM_HF_")

    api_key_admin: str | None = Field(
        default=None,
        validation_alias="HF_API_KEY_ADMIN",
        repr=False,
    )
    base_url: str = "https://api.higgsfield.ai"
    upload_cache_ttl_s: int = 24 * 3600
    remote_check_timeout_s: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "OFM_HF_REMOTE_CHECK_TIMEOUT_S",
            "OFM_KIEAI_REMOTE_CHECK_TIMEOUT_S",
        ),
    )
    video_convert_timeout_s: int = 900


class RunningHubSettings(ProviderSettings):
    model_config = SettingsConfigDict(env_prefix="OFM_RH_")

    api_key_admin: str | None = Field(
        default=None,
        validation_alias="RUNNINGHUB_API_KEY_ADMIN",
        repr=False,
    )
    base_url: str = "https://www.runninghub.ai"
    upload_cache_ttl_s: int = 20 * 3600


class ElyumSettings(ProviderSettings):
    model_config = SettingsConfigDict(env_prefix="OFM_ELYUM_")

    api_key_admin: str | None = Field(
        default=None,
        validation_alias="ELYUM_API_KEY_ADMIN",
        repr=False,
    )
    base_url: str = "https://api.elyum.ai"
    upload_cache_ttl_s: int = 20 * 3600
    ref_image_max_bytes: int = Field(
        default=60 * 1000 * 1000,
        validation_alias="OFM_ELYUM_MAX_FILE_BYTES",
    )
    video_convert_timeout_s: int = 900

    @property
    def max_file_bytes(self) -> int:
        return self.ref_image_max_bytes


class ApiyiSettings(ProviderSettings):
    model_config = SettingsConfigDict(env_prefix="OFM_APIYI_")

    api_key_admin: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "APIYI_API_KEY_WAN",
            "APIYI_API_KEY",
            "APIYI_API_KEY_ADMIN",
        ),
        repr=False,
    )
    api_key_seedance: str | None = Field(
        default=None,
        validation_alias="APIYI_API_KEY_SEEDANCE",
        repr=False,
    )
    api_key_default: str | None = Field(
        default=None,
        validation_alias="APIYI_API_KEY_DEFAULT",
        repr=False,
    )
    base_url: str = "https://api.apiyi.com"
    request_timeout_s: float = 180
    upload_cache_ttl_s: int = 20 * 3600
    key_check_ttl_s: int = 3600
    ref_image_max_bytes: int = 20 * 1000 * 1000
    max_video_bytes: int = 100 * 1000 * 1000
    max_audio_bytes: int = 15 * 1000 * 1000
    link_image_max_bytes: int = 2 * 1000 * 1000
    slow_from_utc_h: int = Field(default=11, ge=0, le=23)
    slow_until_utc_h: int = Field(default=17, ge=0, le=23)
    video_convert_timeout_s: int = 900
    min_poll_interval_s: float = 5
    image_timeout_s: float = 600


class DownloadersSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cookies_file: str = Field(
        default="cookies/cookies.txt",
        validation_alias="OFM_COOKIES_FILE",
    )
    http_timeout_s: int = Field(
        default=60,
        ge=1,
        validation_alias="OFM_DOWNLOAD_HTTP_TIMEOUT_S",
    )
    probe_timeout_s: int = Field(
        default=30,
        ge=1,
        validation_alias="OFM_DOWNLOAD_PROBE_TIMEOUT_S",
    )
    cookies_max_bytes: int = Field(
        default=20 * 1024 * 1024,
        validation_alias="OFM_COOKIES_MAX_BYTES",
    )
    bgutil_pot_provider_url: str | None = None
    cookies_from_browser: str | None = Field(
        default=None,
        validation_alias="OFM_COOKIES_FROM_BROWSER",
    )
    image_download_timeout_s: int = Field(
        default=600,
        validation_alias="OFM_IMAGE_DOWNLOAD_TIMEOUT_S",
    )
    reencode_timeout_s: int = Field(
        default=900,
        ge=1,
        validation_alias="OFM_DOWNLOAD_REENCODE_TIMEOUT_S",
    )
    single_post_items: int = Field(
        default=10,
        ge=1,
        validation_alias="OFM_DOWNLOAD_SINGLE_POST_ITEMS",
    )
    keep_unused_s: int = Field(
        default=86400,
        ge=1,
        validation_alias="OFM_DOWNLOADS_KEEP_UNUSED_S",
    )
    keep_cloned_s: int = Field(
        default=1209600,
        ge=1,
        validation_alias="OFM_DOWNLOADS_KEEP_CLONED_S",
    )
    keep_max_s: int = Field(
        default=2592000,
        ge=1,
        validation_alias="OFM_DOWNLOADS_KEEP_MAX_S",
    )
    sweep_s: int = Field(default=3600, ge=1, validation_alias="OFM_DOWNLOADS_SWEEP_S")
    sweep_enabled: bool | None = Field(
        default=None,
        validation_alias="OFM_DOWNLOADS_SWEEP",
    )

    sheet_name: str = Field(
        default="content_links",
        validation_alias="OFM_DOWNLOADS_SHEET_NAME",
    )
    sheet_cooldown_s: int = Field(
        default=60,
        ge=1,
        validation_alias="OFM_DOWNLOADS_SHEET_COOLDOWN_S",
    )
    sheet_claim_s: int = Field(
        default=259200,
        ge=1,
        validation_alias="OFM_DOWNLOADS_SHEET_CLAIM_S",
    )

    def sweeping(self, production: bool) -> bool:  # noqa: FBT001
        return production if self.sweep_enabled is None else self.sweep_enabled


class InstagramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_id: str = Field(default="936619743392459", validation_alias="OFM_IG_APP_ID")
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
        ),
        validation_alias="OFM_IG_USER_AGENT",
    )
    asbd_id: str = Field(default="359341", validation_alias="OFM_IG_ASBD_ID")
    profile_doc_id: str = Field(
        default="28036671149327607",
        validation_alias="OFM_IG_PROFILE_DOC_ID",
    )
    reels_doc_id: str = Field(
        default="28989741540630190",
        validation_alias="OFM_IG_REELS_DOC_ID",
    )
    media_doc_id: str = Field(
        default="27130156389949648",
        validation_alias="OFM_IG_MEDIA_DOC_ID",
    )
    timeline_doc_id: str = Field(
        default="28142289515441884",
        validation_alias="OFM_IG_TIMELINE_DOC_ID",
    )
    feed_page_size: int = Field(
        default=24,
        ge=1,
        validation_alias="OFM_IG_FEED_PAGE_SIZE",
    )
    feed_thumb_px: int = Field(
        default=270,
        ge=64,
        validation_alias="OFM_IG_FEED_THUMB_PX",
    )
    timeout_s: int = Field(default=20, validation_alias="OFM_IG_TIMEOUT_S")
    proxies: str = Field(default="", validation_alias="OFM_IG_PROXIES")
    proxy_retries: int = Field(default=3, ge=0, validation_alias="OFM_IG_PROXY_RETRIES")
    retry_jitter_s: float = Field(
        default=2.0,
        ge=0,
        validation_alias="OFM_IG_RETRY_JITTER_S",
    )
    http_retries: int = Field(default=2, ge=0, validation_alias="OFM_IG_HTTP_RETRIES")
    retry_backoff_s: float = Field(
        default=0.5,
        ge=0,
        validation_alias="OFM_IG_RETRY_BACKOFF_S",
    )

    @property
    def proxy_urls(self) -> list[str]:
        return [
            p if "://" in p else f"http://{p}"
            for p in (part.strip() for part in self.proxies.split(","))
            if p
        ]

    max_age_days: float = Field(
        default=14,
        gt=0,
        validation_alias="OFM_IG_MAX_AGE_DAYS",
    )
    reels_per_profile: int = Field(
        default=120,
        ge=1,
        validation_alias="OFM_IG_REELS_PER_PROFILE",
    )
    page_size: int = Field(default=12, ge=1, validation_alias="OFM_IG_PAGE_SIZE")
    min_views: int = Field(default=50_000, ge=0, validation_alias="OFM_IG_MIN_VIEWS")
    max_duration_s: float = Field(
        default=15.0,
        gt=0,
        validation_alias="OFM_IG_MAX_DURATION_S",
    )
    rescan_enabled: bool = Field(
        default=False,
        validation_alias="OFM_IG_RESCAN_ENABLED",
    )
    rescan_interval_s: int = Field(
        default=12 * 60 * 60,
        validation_alias="OFM_IG_RESCAN_INTERVAL_S",
    )
    rescan_check_interval_s: int = Field(
        default=600,
        validation_alias="OFM_IG_RESCAN_CHECK_INTERVAL_S",
    )
    rescan_claim_ttl_s: int = Field(
        default=60 * 60,
        validation_alias="OFM_IG_RESCAN_CLAIM_TTL_S",
    )
    download_workers: int = Field(
        default=6,
        ge=1,
        validation_alias="OFM_IG_DOWNLOAD_WORKERS",
    )
    track_max_reels: int = Field(
        default=96,
        ge=1,
        validation_alias="OFM_IG_TRACK_MAX_REELS",
    )


class ProfilePageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    )
    timeout_s: float = Field(default=20, gt=0)
    proxies: str = ""
    http_retries: int = Field(default=2, ge=0)
    retry_backoff_s: float = Field(default=0.5, ge=0)

    @property
    def proxy_urls(self) -> list[str]:
        return [
            p if "://" in p else f"http://{p}"
            for p in (part.strip() for part in self.proxies.split(","))
            if p
        ]


class SnapchatSettings(ProfilePageSettings):
    model_config = SettingsConfigDict(env_prefix="OFM_SNAPCHAT_")


class XSettings(ProfilePageSettings):
    model_config = SettingsConfigDict(env_prefix="OFM_X_")


class AccountsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    rescan_interval_s: int = Field(
        default=6 * 60 * 60,
        ge=60,
        validation_alias="OFM_ACCOUNTS_RESCAN_INTERVAL_S",
    )
    max_rescan_interval_s: int = Field(
        default=24 * 60 * 60,
        ge=60,
        validation_alias="OFM_ACCOUNTS_MAX_RESCAN_INTERVAL_S",
    )
    rescan_check_interval_s: int = Field(
        default=300,
        ge=10,
        validation_alias="OFM_ACCOUNTS_RESCAN_CHECK_INTERVAL_S",
    )
    claim_ttl_s: int = Field(
        default=2 * 60 * 60,
        ge=60,
        validation_alias="OFM_ACCOUNTS_CLAIM_TTL_S",
    )
    scan_times: str = Field(
        default="13:50,18:00",
        validation_alias="OFM_ACCOUNTS_SCAN_TIMES",
    )
    scan_timezone: str = Field(
        default="Europe/Warsaw",
        validation_alias="OFM_ACCOUNTS_SCAN_TIMEZONE",
    )
    scheduled_scans: bool | None = Field(
        default=None,
        validation_alias="OFM_ACCOUNTS_SCHEDULED_SCANS",
    )

    def scheduled(self, production: bool) -> bool:  # noqa: FBT001
        return production if self.scheduled_scans is None else self.scheduled_scans

    @field_validator("scan_times")
    @classmethod
    def _hh_mm_list(cls, value: str) -> str:
        found = []
        for part in (p.strip() for p in value.split(",")):
            if not part:
                continue
            try:
                hour, minute = (int(n) for n in part.split(":"))
                dt.time(hour, minute)
            except ValueError:
                raise ValueError(_SCAN_TIMES_MSG) from None
            found.append(f"{hour:02d}:{minute:02d}")
        return ",".join(found)

    def scan_time_list(self) -> list[dt.time]:
        return [
            dt.time(int(t[:2]), int(t[3:])) for t in self.scan_times.split(",") if t
        ]

    sources: str = Field(
        default="api,proxy,login",
        validation_alias="OFM_ACCOUNTS_SOURCES",
    )

    def source_list(self) -> list[str]:
        return [s.strip() for s in self.sources.split(",") if s.strip()]

    apify_days: int = Field(default=7, ge=1, validation_alias="OFM_ACCOUNTS_APIFY_DAYS")
    apify_profile_actor: str = Field(
        default="apify~instagram-profile-scraper",
        validation_alias="OFM_ACCOUNTS_APIFY_PROFILE_ACTOR",
    )
    apify_reel_actor: str = Field(
        default="apify~instagram-reel-scraper",
        validation_alias="OFM_ACCOUNTS_APIFY_REEL_ACTOR",
    )
    apify_usd_per_item: float = Field(
        default=0.0026,
        ge=0,
        validation_alias="OFM_ACCOUNTS_APIFY_USD_PER_ITEM",
    )
    apify_usd_per_run: float = Field(
        default=0.001,
        ge=0,
        validation_alias="OFM_ACCOUNTS_APIFY_USD_PER_RUN",
    )
    apify_est_reels: int = Field(
        default=15,
        ge=0,
        validation_alias="OFM_ACCOUNTS_APIFY_EST_REELS",
    )
    apify_run_timeout_s: int = Field(
        default=600,
        ge=60,
        validation_alias="OFM_ACCOUNTS_APIFY_RUN_TIMEOUT_S",
    )
    apify_x_actor: str = Field(
        default="kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest",
        validation_alias="OFM_ACCOUNTS_APIFY_X_ACTOR",
    )
    apify_x_usd_per_run: float = Field(
        default=0.01,
        gt=0,
        validation_alias="OFM_ACCOUNTS_APIFY_X_USD_PER_RUN",
    )
    apify_x_max_items: int = Field(
        default=40,
        ge=20,
        validation_alias="OFM_ACCOUNTS_APIFY_X_MAX_ITEMS",
    )

    batch_size: int = Field(
        default=20,
        ge=1,
        validation_alias="OFM_ACCOUNTS_BATCH_SIZE",
    )
    proxy_retries: int = Field(
        default=3,
        ge=0,
        validation_alias="OFM_ACCOUNTS_PROXY_RETRIES",
    )
    retry_jitter_s: float = Field(
        default=2.0,
        ge=0,
        validation_alias="OFM_ACCOUNTS_RETRY_JITTER_S",
    )
    periods_days: list[int] = Field(
        default=[1, 7, 14, 30],
        min_length=1,
        validation_alias="OFM_ACCOUNTS_PERIODS_DAYS",
    )
    default_period_days: int = Field(
        default=7,
        ge=1,
        validation_alias="OFM_ACCOUNTS_DEFAULT_PERIOD_DAYS",
    )
    history_days: int = Field(
        default=90,
        ge=1,
        validation_alias="OFM_ACCOUNTS_HISTORY_DAYS",
    )
    top_posts: int = Field(default=10, ge=1, validation_alias="OFM_ACCOUNTS_TOP_POSTS")
    page_sizes: list[int] = Field(
        default=[25, 50, 100, 200],
        min_length=1,
        validation_alias="OFM_ACCOUNTS_PAGE_SIZES",
    )
    default_page_size: int = Field(
        default=50,
        ge=1,
        validation_alias="OFM_ACCOUNTS_DEFAULT_PAGE_SIZE",
    )
    max_add: int = Field(default=500, ge=1, validation_alias="OFM_ACCOUNTS_MAX_ADD")

    @property
    def post_retention_days(self) -> int:
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
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    admin_emails_raw: str = Field(default="", validation_alias="OFM_ADMIN_EMAILS")
    password_admin: str = Field(default="admin", validation_alias="OFM_PASSWORD_ADMIN")
    owner_provider: str = Field(default="owner", validation_alias="OFM_OWNER_PROVIDER")

    google_client_id: str = Field(default="", validation_alias="OFM_GOOGLE_CLIENT_ID")
    google_client_secret: SecretStr | None = Field(
        default=None,
        validation_alias="OFM_GOOGLE_CLIENT_SECRET",
    )

    apple_services_id: str = Field(default="", validation_alias="OFM_APPLE_SERVICES_ID")
    apple_team_id: str = Field(default="", validation_alias="OFM_APPLE_TEAM_ID")
    apple_key_id: str = Field(default="", validation_alias="OFM_APPLE_KEY_ID")
    apple_private_key: SecretStr | None = Field(
        default=None,
        validation_alias="OFM_APPLE_PRIVATE_KEY",
    )

    password_min_length: int = Field(
        default=10,
        ge=8,
        validation_alias="OFM_PASSWORD_MIN_LENGTH",
    )
    password_link_ttl_s: int = Field(
        default=7 * 86400,
        ge=600,
        validation_alias="OFM_PASSWORD_LINK_TTL_S",
    )
    password_hash_concurrency: int = Field(
        default=4,
        ge=1,
        validation_alias="OFM_PASSWORD_HASH_CONCURRENCY",
    )
    state_ttl_s: int = Field(
        default=600,
        ge=60,
        validation_alias="OFM_SIGNIN_STATE_TTL_S",
    )

    @property
    def admin_emails(self) -> list[str]:
        found = (p.strip().lower() for p in self.admin_emails_raw.split(","))
        return list(dict.fromkeys(e for e in found if e))

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def apple_configured(self) -> bool:
        return bool(
            self.apple_services_id
            and self.apple_team_id
            and self.apple_key_id
            and self.apple_private_key,
        )


class TelemetrySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    token: SecretStr | None = Field(default=None, validation_alias="LOGFIRE_TOKEN")
    release: str = Field(default="dev", validation_alias="OFM_RELEASE")
    flush_timeout_s: float = Field(
        default=5.0,
        gt=0,
        validation_alias="OFM_TELEMETRY_FLUSH_S",
    )

    @field_validator("token", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


class SentrySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    dsn: SecretStr | None = Field(default=None, validation_alias="SENTRY_DSN")

    @field_validator("dsn", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


class LinksSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    oopsie_api_key_admin: str | None = Field(
        default=None,
        validation_alias="OOPSIE_API_KEY_ADMIN",
        repr=False,
    )
    bouncy_api_key_admin: str | None = Field(
        default=None,
        validation_alias="BOUNCY_API_KEY_ADMIN",
        repr=False,
    )
    request_timeout_s: float = Field(
        default=30,
        gt=0,
        validation_alias="OFM_LINKS_REQUEST_TIMEOUT_S",
    )
    sync_interval_s: int = Field(
        default=3 * 60 * 60,
        ge=60,
        validation_alias="OFM_LINKS_SYNC_INTERVAL_S",
    )
    sync_check_interval_s: int = Field(
        default=300,
        ge=10,
        validation_alias="OFM_LINKS_SYNC_CHECK_INTERVAL_S",
    )
    background_sync: bool | None = Field(
        default=None,
        validation_alias="OFM_LINKS_BACKGROUND_SYNC",
    )

    @field_validator("background_sync", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    def syncing_in_background(self, production: bool) -> bool:  # noqa: FBT001
        return production if self.background_sync is None else self.background_sync

    claim_ttl_s: int = Field(
        default=60 * 60,
        ge=60,
        validation_alias="OFM_LINKS_CLAIM_TTL_S",
    )
    create_ttl_s: int = Field(
        default=30 * 60,
        ge=60,
        validation_alias="OFM_LINKS_CREATE_TTL_S",
    )
    periods_days: list[int] = Field(
        default=[1, 7, 30, 90],
        min_length=1,
        validation_alias="OFM_LINKS_PERIODS_DAYS",
    )
    default_period_days: int = Field(
        default=7,
        ge=1,
        validation_alias="OFM_LINKS_DEFAULT_PERIOD_DAYS",
    )
    page_sizes: list[int] = Field(
        default=[25, 50, 100, 200],
        min_length=1,
        validation_alias="OFM_LINKS_PAGE_SIZES",
    )
    default_page_size: int = Field(
        default=50,
        ge=1,
        validation_alias="OFM_LINKS_DEFAULT_PAGE_SIZE",
    )
    max_generate: int = Field(
        default=100,
        ge=1,
        validation_alias="OFM_LINKS_MAX_GENERATE",
    )
    self_link_limit: int | None = Field(
        default=None,
        ge=1,
        validation_alias="OFM_LINKS_SELF_LIMIT",
    )
    bridge_rate_per_min: int = Field(
        default=240,
        ge=1,
        validation_alias="OFM_LINKS_BRIDGE_RATE_PER_MIN",
    )
    tunnel_host_suffixes: str = Field(
        default="",
        validation_alias="OFM_LINKS_TUNNEL_HOST_SUFFIXES",
    )

    def tunnel_suffixes(self) -> list[str]:
        parts = (p.strip().lower() for p in self.tunnel_host_suffixes.split(","))
        return [p if p.startswith(".") else f".{p}" for p in parts if p]

    @property
    def history_days(self) -> int:
        return max(*self.periods_days, 30)

    def admin_key(self, provider: str) -> str | None:
        keys = {
            "oopsie": self.oopsie_api_key_admin,
            "bouncy": self.bouncy_api_key_admin,
        }
        return keys.get(provider) or None

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
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    keys: SecretStr | None = Field(default=None, validation_alias="OFM_VAULT_KEY")
    history_limit: int = Field(
        default=200,
        ge=1,
        le=1000,
        validation_alias="OFM_PHONES_HISTORY_LIMIT",
    )
    backup_keep: int = Field(
        default=30,
        ge=1,
        validation_alias="OFM_PHONES_BACKUP_KEEP",
    )
    backup_sweep_s: int = Field(
        default=300,
        ge=10,
        validation_alias="OFM_PHONES_BACKUP_SWEEP_S",
    )

    def key_list(self) -> list[str]:
        if self.keys is None:
            return []
        return [k.strip() for k in self.keys.get_secret_value().split(",") if k.strip()]


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    level: str = Field(default="INFO", validation_alias="OFM_LOG_LEVEL")
    format: str = Field(default="text", validation_alias="OFM_LOG_FORMAT")
    access_log: bool = Field(default=True, validation_alias="OFM_LOG_ACCESS")


class ExportSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    default: str = Field(default="capcut", validation_alias="OFM_EXPORT_DEFAULT")
    mirror: bool = Field(default=False, validation_alias="OFM_EXPORT_MIRROR")
    attempts: int = Field(default=3, ge=1, validation_alias="OFM_EXPORT_ATTEMPTS")
    reruns: int = Field(default=3, ge=1, validation_alias="OFM_EXPORT_RERUNS")
    timeout_s: int = Field(
        default=600,
        validation_alias=AliasChoices(
            "OFM_EXPORT_TIMEOUT_S",
            "OFM_CLONE_CAPCUT_TIMEOUT_S",
        ),
    )
    device_timezone: str = Field(
        default="America/New_York",
        validation_alias="OFM_EXPORT_DEVICE_TIMEZONE",
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
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    max_video_s: int = Field(default=600, ge=1, validation_alias="OFM_CLONE_LONG_MAX_S")
    parallel_pieces: int = Field(
        default=5,
        ge=1,
        validation_alias="OFM_CLONE_LONG_PARALLEL",
    )
    ffmpeg_timeout_s: int = Field(
        default=900,
        ge=1,
        validation_alias="OFM_CLONE_LONG_FFMPEG_TIMEOUT_S",
    )
    piece_max_side: int = Field(
        default=1080,
        ge=2,
        validation_alias="OFM_CLONE_LONG_PIECE_MAX_SIDE",
    )
    colour_match: bool = Field(
        default=False,
        validation_alias="OFM_CLONE_LONG_COLOUR_MATCH",
    )
    chain: bool = Field(default=False, validation_alias="OFM_CLONE_LONG_CHAIN")
    audio: bool = Field(default=False, validation_alias="OFM_CLONE_LONG_AUDIO")


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    default_video: str = Field(
        default="gemini:gemini-3.6-flash",
        min_length=1,
        validation_alias="LLM_DEFAULT_VIDEO",
    )
    fallback_video: str = Field(
        default=(
            "gemini:gemini-3.8-flash,gemini:gemini-3.5-flash,"
            "gemini:gemini-3.5-flash-lite"
        ),
        validation_alias="LLM_FALLBACK_VIDEO",
    )
    default_chat: str = Field(
        default="groq",
        min_length=1,
        validation_alias="LLM_DEFAULT_CHAT",
    )

    @property
    def gemini_keys(self) -> list[str]:
        raw = self.gemini_api_key.get_secret_value() if self.gemini_api_key else ""
        keys = (k.strip() for k in raw.replace("\n", ",").split(","))
        return list(dict.fromkeys(k for k in keys if k))

    @property
    def video_chain(self) -> list[str]:
        names = [self.default_video, *self.fallback_video.split(",")]
        return list(dict.fromkeys(n.strip() for n in names if n.strip()))

    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
    )
    gemini_model: str = Field(
        default="gemini-3.8-flash",
        min_length=1,
        validation_alias="OFM_GEMINI_MODEL",
    )
    groq_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GROQ_API_KEY",
    )
    groq_model: str = Field(
        default="qwen/qwen3.8-27b",
        min_length=1,
        validation_alias="OFM_GROQ_MODEL",
    )
    cerebras_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="CEREBRAS_API_KEY",
    )
    cerebras_model: str = Field(
        default="qwen-3.8-27b",
        min_length=1,
        validation_alias="OFM_CEREBRAS_MODEL",
    )
    image_max_side: int = Field(
        default=1024,
        ge=64,
        validation_alias="OFM_LLM_IMAGE_MAX_SIDE",
    )
    image_max_bytes: int = Field(
        default=3 * 1024 * 1024,
        ge=1024,
        validation_alias="OFM_LLM_IMAGE_MAX_BYTES",
    )
    video_sheet_frames: int = Field(
        default=9,
        ge=1,
        le=36,
        validation_alias="OFM_LLM_VIDEO_SHEET_FRAMES",
    )
    video_sheet_frame_px: int = Field(
        default=360,
        ge=64,
        validation_alias="OFM_LLM_VIDEO_SHEET_FRAME_PX",
    )
    video_sheet_timeout_s: int = Field(
        default=60,
        ge=1,
        validation_alias="OFM_LLM_VIDEO_SHEET_TIMEOUT_S",
    )
    timeout_s: float = Field(default=300, gt=0, validation_alias="OFM_LLM_TIMEOUT_S")
    retries: int = Field(default=4, ge=0, validation_alias="OFM_LLM_RETRIES")
    shape_retries: int = Field(
        default=1,
        ge=0,
        validation_alias="OFM_LLM_SHAPE_RETRIES",
    )
    retry_wait_s: float = Field(
        default=5,
        ge=0,
        validation_alias="OFM_LLM_RETRY_WAIT_S",
    )
    retry_max_wait_s: float = Field(
        default=60,
        ge=0,
        validation_alias="OFM_LLM_RETRY_MAX_WAIT_S",
    )
    inline_max_bytes: int = Field(
        default=14 * 1024 * 1024,
        ge=0,
        validation_alias="OFM_LLM_INLINE_MAX_BYTES",
    )
    file_ready_timeout_s: float = Field(
        default=120,
        gt=0,
        validation_alias="OFM_LLM_FILE_READY_TIMEOUT_S",
    )


class CloneAiCheckSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    every_s: float = Field(
        default=1.0,
        gt=0,
        validation_alias="OFM_CLONE_AI_CHECK_EVERY_S",
    )
    max_frames: int = Field(
        default=16,
        ge=2,
        le=36,
        validation_alias="OFM_CLONE_AI_CHECK_MAX_FRAMES",
    )
    frame_px: int = Field(
        default=360,
        ge=64,
        validation_alias="OFM_CLONE_AI_CHECK_FRAME_PX",
    )
    ffmpeg_timeout_s: int = Field(
        default=60,
        ge=1,
        validation_alias="OFM_CLONE_AI_CHECK_FFMPEG_TIMEOUT_S",
    )
    deadline_s: float = Field(
        default=600,
        gt=0,
        validation_alias="OFM_CLONE_AI_CHECK_DEADLINE_S",
    )
    glitchy_percent: int = Field(
        default=50,
        ge=1,
        le=100,
        validation_alias="OFM_CLONE_AI_CHECK_GLITCHY_PERCENT",
    )


class CloneLinksSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    max_links: int = Field(default=120, ge=1, validation_alias="OFM_CLONE_LINKS_MAX")
    info_ttl_s: int = Field(
        default=30 * 24 * 3600,
        ge=60,
        validation_alias="OFM_CLONE_LINKS_INFO_TTL_S",
    )
    drive_min_free_mb: int = Field(
        default=500,
        ge=0,
        validation_alias="OFM_CLONE_LINKS_DRIVE_MIN_FREE_MB",
    )
    drive_space_ttl_s: int = Field(
        default=300,
        ge=10,
        validation_alias="OFM_CLONE_LINKS_DRIVE_SPACE_TTL_S",
    )
    preview_parallel: int = Field(
        default=6,
        ge=1,
        le=64,
        validation_alias="OFM_CLONE_LINKS_PREVIEW_PARALLEL",
    )
    preview_deadline_s: int = Field(
        default=90,
        ge=5,
        validation_alias="OFM_CLONE_LINKS_PREVIEW_DEADLINE_S",
    )
    preview_socket_timeout_s: float = Field(
        default=10,
        gt=0,
        validation_alias="OFM_CLONE_LINKS_PREVIEW_SOCKET_TIMEOUT_S",
    )
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
    reel_mb: int = Field(default=55, ge=1, validation_alias="OFM_CLONE_LINKS_REEL_MB")
    halt_ttl_s: int = Field(
        default=3600,
        ge=60,
        validation_alias="OFM_CLONE_LINKS_HALT_TTL_S",
    )
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
    review_timeout_s: int = Field(
        default=1200,
        ge=60,
        validation_alias="OFM_CLONE_LINKS_REVIEW_TIMEOUT_S",
    )
    review_stale_s: int = Field(
        default=1800,
        ge=60,
        validation_alias="OFM_CLONE_LINKS_REVIEW_STALE_S",
    )
    review_lost_s: int = Field(
        default=6 * 3600,
        ge=60,
        validation_alias="OFM_CLONE_LINKS_REVIEW_LOST_S",
    )
    review_attempts: int = Field(
        default=3,
        ge=1,
        validation_alias="OFM_CLONE_LINKS_REVIEW_ATTEMPTS",
    )


class GDriveSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_account: SecretStr | None = Field(
        default=None,
        validation_alias="OFM_GDRIVE_SERVICE_ACCOUNT",
    )
    root_folder_id: str = Field(default="", validation_alias="OFM_GDRIVE_ROOT_FOLDER")
    read_live: bool = Field(default=False, validation_alias="OFM_DRIVE_READ_LIVE")
    sweep_enabled: bool | None = Field(default=None, validation_alias="OFM_DRIVE_SWEEP")
    posted_folder_name: str = Field(
        default=AccountFolder.POSTED,
        min_length=1,
        validation_alias="OFM_GDRIVE_POSTED_FOLDER",
    )
    timezone: str = Field(
        default="Europe/Warsaw",
        validation_alias="OFM_GDRIVE_TIMEZONE",
    )
    posting_time_utc: dt.time = Field(
        default=dt.time(19, 45),
        validation_alias="OFM_GDRIVE_POSTING_TIME_UTC",
    )
    posting_window_before_h: float = Field(
        default=1,
        ge=0,
        le=23,
        validation_alias="OFM_GDRIVE_POSTING_BEFORE_H",
    )
    posting_window_after_h: float = Field(
        default=5,
        ge=0,
        le=23,
        validation_alias="OFM_GDRIVE_POSTING_AFTER_H",
    )
    check_interval_s: int = Field(
        default=300,
        ge=30,
        validation_alias="OFM_GDRIVE_CHECK_INTERVAL_S",
    )
    retries: int = Field(default=5, ge=0, validation_alias="OFM_GDRIVE_RETRIES")
    interactive_timeout_s: float = Field(
        default=10,
        gt=0,
        validation_alias="OFM_GDRIVE_INTERACTIVE_TIMEOUT_S",
    )
    interactive_retries: int = Field(
        default=1,
        ge=0,
        validation_alias="OFM_GDRIVE_INTERACTIVE_RETRIES",
    )
    sheet_max_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1,
        validation_alias="OFM_GDRIVE_SHEET_MAX_BYTES",
    )
    list_workers: int = Field(
        default=8,
        ge=1,
        validation_alias="OFM_GDRIVE_LIST_WORKERS",
    )
    thumb_px: int = Field(default=400, ge=32, validation_alias="OFM_GDRIVE_THUMB_PX")
    upload_stale_s: int = Field(
        default=3600,
        ge=60,
        validation_alias="OFM_GDRIVE_UPLOAD_STALE_S",
    )
    space_retry_s: int = Field(
        default=60,
        ge=5,
        validation_alias="OFM_GDRIVE_SPACE_RETRY_S",
    )
    review_lock_s: int = Field(
        default=60,
        ge=1,
        validation_alias="OFM_GDRIVE_REVIEW_LOCK_S",
    )
    review_folder_ttl_s: int = Field(
        default=7 * 24 * 3600,
        ge=60,
        validation_alias="OFM_GDRIVE_REVIEW_FOLDER_TTL_S",
    )

    def sweeping(self, production: bool) -> bool:  # noqa: FBT001
        return production if self.sweep_enabled is None else self.sweep_enabled

    def live_read(self, production: bool) -> bool:  # noqa: FBT001
        return self.read_live and not production


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    scan_stale_s: int = Field(
        default=600,
        ge=30,
        validation_alias="OFM_STORAGE_SCAN_STALE_S",
    )
    scan_timeout_s: int = Field(
        default=1800,
        ge=60,
        validation_alias="OFM_STORAGE_SCAN_TIMEOUT_S",
    )
    clean_timeout_s: int = Field(
        default=3600,
        ge=60,
        validation_alias="OFM_STORAGE_CLEAN_TIMEOUT_S",
    )
    start_lock_s: int = Field(default=10, ge=1, validation_alias="OFM_STORAGE_LOCK_S")


class RepurposeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    sscd_model: str | None = Field(default=None, validation_alias="OFM_SSCD_MODEL")
    sscd_threshold: float = Field(
        default=0.75,
        gt=0,
        le=1,
        validation_alias="OFM_SSCD_THRESHOLD",
    )
    sscd_threads: int = Field(default=2, ge=1, validation_alias="OFM_SSCD_THREADS")
    tmk_dir: str | None = Field(default=None, validation_alias="OFM_TMK_DIR")
    lab_vmaf_ffmpeg: str | None = Field(
        default=None,
        validation_alias="OFM_LAB_VMAF_FFMPEG",
    )
    snapchat_font: str | None = Field(
        default=None,
        validation_alias="OFM_SNAPCHAT_FONT",
    )
    job_timeout_s: int = Field(
        default=5400,
        ge=60,
        validation_alias="OFM_REPURPOSE_JOB_TIMEOUT_S",
    )
    max_tries: int = Field(
        default=10,
        ge=1,
        le=10,
        validation_alias="OFM_REPURPOSE_MAX_TRIES",
    )
    max_copies: int = Field(
        default=5,
        ge=1,
        validation_alias="OFM_REPURPOSE_MAX_COPIES",
    )


class TrialsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    per_reel_s: int = Field(default=900, ge=1, validation_alias="OFM_TRIALS_PER_REEL_S")
    job_grace_s: int = Field(
        default=300,
        ge=0,
        validation_alias="OFM_TRIALS_JOB_GRACE_S",
    )
    keep_s: int = Field(default=86400, ge=1, validation_alias="OFM_TRIALS_KEEP_S")
    remake_s: int = Field(default=400, ge=1, validation_alias="OFM_TRIALS_REMAKE_S")
    sweep_s: int = Field(default=3600, ge=1, validation_alias="OFM_TRIALS_SWEEP_S")


class ViralSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    apify_api_keys: str = Field(
        default="",
        validation_alias="APIFY_API_KEYS",
        repr=False,
    )
    actor: str = Field(
        default="apify~instagram-reel-scraper",
        validation_alias="OFM_VIRAL_ACTOR",
    )
    scan: int = Field(default=200, ge=1, le=5000, validation_alias="OFM_VIRAL_SCAN")
    top: int = Field(default=30, ge=1, le=200, validation_alias="OFM_VIRAL_TOP")
    usd_per_reel: float = Field(
        default=0.0026,
        ge=0,
        validation_alias="OFM_VIRAL_USD_PER_REEL",
    )
    usd_per_run: float = Field(
        default=0.001,
        ge=0,
        validation_alias="OFM_VIRAL_USD_PER_RUN",
    )
    run_timeout_s: int = Field(
        default=900,
        ge=60,
        validation_alias="OFM_VIRAL_RUN_TIMEOUT_S",
    )
    download_workers: int = Field(
        default=6,
        ge=1,
        le=32,
        validation_alias="OFM_VIRAL_DOWNLOAD_WORKERS",
    )
    download_timeout_s: float = Field(
        default=60,
        gt=0,
        validation_alias="OFM_VIRAL_DOWNLOAD_TIMEOUT_S",
    )

    keep_s: int = Field(default=3 * 86400, ge=60, validation_alias="OFM_VIRAL_KEEP_S")
    sweep_s: int = Field(default=3600, ge=60, validation_alias="OFM_VIRAL_SWEEP_S")

    @property
    def api_keys(self) -> list[str]:
        return [k.strip() for k in self.apify_api_keys.split(",") if k.strip()]

    @property
    def max_usd(self) -> float:
        return self.scan * self.usd_per_reel + self.usd_per_run


class ChattingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    unanswered_after_s: int = Field(
        default=300,
        ge=1,
        validation_alias="OFM_CHATTING_UNANSWERED_AFTER_S",
    )
    lookback_s: int = Field(
        default=86400,
        ge=1,
        validation_alias="OFM_CHATTING_LOOKBACK_S",
    )
    check_interval_s: float = Field(
        default=60,
        gt=0,
        validation_alias="OFM_CHATTING_CHECK_INTERVAL_S",
    )
    wait_events_check_s: float = Field(
        default=600,
        gt=0,
        validation_alias="OFM_CHATTING_WAIT_EVENTS_CHECK_S",
    )
    wait_recheck_poll_s: float = Field(
        default=15,
        gt=0,
        validation_alias="OFM_CHATTING_WAIT_RECHECK_POLL_S",
    )
    events_platforms_off: str = Field(
        default="fanvue",
        validation_alias="OFM_CHATTING_EVENTS_PLATFORMS_OFF",
    )

    def events_off(self) -> set[str]:
        return {p.strip() for p in self.events_platforms_off.split(",") if p.strip()}

    webhook_base_url: str = Field(
        default="",
        validation_alias="OFM_CHATTING_WEBHOOK_BASE_URL",
    )
    webhook_sync_interval_s: float = Field(
        default=3600,
        gt=0,
        validation_alias="OFM_CHATTING_WEBHOOK_SYNC_INTERVAL_S",
    )
    webhook_quiet_s: int = Field(
        default=600,
        ge=1,
        validation_alias="OFM_CHATTING_WEBHOOK_QUIET_S",
    )
    webhook_tolerance_s: int = Field(
        default=300,
        ge=1,
        validation_alias="OFM_CHATTING_WEBHOOK_TOLERANCE_S",
    )
    webhook_max_body_bytes: int = Field(
        default=1_048_576,
        ge=1,
        validation_alias="OFM_CHATTING_WEBHOOK_MAX_BODY_BYTES",
    )
    webhook_max_refused: int = Field(
        default=30,
        ge=1,
        validation_alias="OFM_CHATTING_WEBHOOK_MAX_REFUSED",
    )
    webhook_refused_window_s: int = Field(
        default=300,
        ge=1,
        validation_alias="OFM_CHATTING_WEBHOOK_REFUSED_WINDOW_S",
    )
    sale_notify_cents: int = Field(
        default=5000,
        ge=0,
        validation_alias="OFM_CHATTING_SALE_NOTIFY_CENTS",
    )
    mass_min_chats: int = Field(
        default=50,
        ge=2,
        validation_alias="OFM_CHATTING_MASS_MIN_CHATS",
    )
    mass_window_s: int = Field(
        default=600,
        ge=1,
        validation_alias="OFM_CHATTING_MASS_WINDOW_S",
    )
    mass_gap_s: int = Field(
        default=3600,
        ge=1,
        validation_alias="OFM_CHATTING_MASS_GAP_S",
    )
    notification_shown_s: int = Field(
        default=86400,
        ge=1,
        validation_alias="OFM_CHATTING_NOTIFICATION_SHOWN_S",
    )
    recent_models_shown: int = Field(
        default=3,
        ge=1,
        validation_alias="OFM_CHATTING_RECENT_MODELS",
    )
    reached_keep_s: int = Field(
        default=3600,
        ge=60,
        validation_alias="OFM_CHATTING_REACHED_KEEP_S",
    )
    sync_max_failed_chats: int = Field(
        default=5,
        ge=1,
        validation_alias="OFM_CHATTING_SYNC_MAX_FAILED_CHATS",
    )


class FanvueSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    client_id: str = Field(default="", validation_alias="FANVUE_CLIENT_ID")
    client_secret: str = Field(
        default="",
        repr=False,
        validation_alias="FANVUE_CLIENT_SECRET",
    )
    event_secret: str = Field(
        default="",
        repr=False,
        validation_alias="FANVUE_EVENT_SECRET",
    )
    api_url: str = Field(
        default="https://api.fanvue.com/v1",
        validation_alias="OFM_FANVUE_API_URL",
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
        default="2025-06-26",
        validation_alias="OFM_FANVUE_API_VERSION",
    )
    scopes: str = Field(
        default="openid offline_access offline read:self read:chat read:creator",
        validation_alias="OFM_FANVUE_SCOPES",
    )
    timeout_s: float = Field(default=30, gt=0, validation_alias="OFM_FANVUE_TIMEOUT_S")
    page_size: int = Field(
        default=50,
        ge=1,
        le=50,
        validation_alias="OFM_FANVUE_PAGE_SIZE",
    )
    tries: int = Field(default=4, ge=1, validation_alias="OFM_FANVUE_TRIES")
    retry_wait_s: float = Field(
        default=2.0,
        ge=0,
        validation_alias="OFM_FANVUE_RETRY_WAIT_S",
    )
    max_wait_s: float = Field(
        default=65.0,
        ge=0,
        validation_alias="OFM_FANVUE_MAX_WAIT_S",
    )
    token_refresh_margin_s: int = Field(
        default=120,
        ge=0,
        validation_alias="OFM_FANVUE_TOKEN_REFRESH_MARGIN_S",
    )
    refresh_tries: int = Field(
        default=3,
        ge=1,
        validation_alias="OFM_FANVUE_REFRESH_TRIES",
    )
    connect_ttl_s: int = Field(
        default=600,
        ge=1,
        validation_alias="OFM_FANVUE_CONNECT_TTL_S",
    )


class DropboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_key: str = Field(default="", validation_alias="DROPBOX_APP_KEY")
    app_secret: SecretStr | None = Field(
        default=None,
        validation_alias="DROPBOX_APP_SECRET",
    )
    test_refresh_token: SecretStr | None = Field(
        default=None,
        validation_alias="OFM_DROPBOX_TEST_REFRESH_TOKEN",
    )
    timeout_s: float = Field(default=60, gt=0, validation_alias="OFM_DROPBOX_TIMEOUT_S")
    interactive_timeout_s: float = Field(
        default=15,
        gt=0,
        validation_alias="OFM_DROPBOX_INTERACTIVE_TIMEOUT_S",
    )
    tries: int = Field(default=4, ge=1, validation_alias="OFM_DROPBOX_TRIES")
    connect_ttl_s: int = Field(
        default=600,
        ge=60,
        validation_alias="OFM_DROPBOX_CONNECT_TTL_S",
    )


class StackedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_url: str = Field(
        default="https://api.stacked.com/external-service/v1",
        validation_alias="OFM_STACKED_API_URL",
    )
    timeout_s: float = Field(default=30, gt=0, validation_alias="OFM_STACKED_TIMEOUT_S")
    page_size: int = Field(
        default=100,
        ge=1,
        le=100,
        validation_alias="OFM_STACKED_PAGE_SIZE",
    )
    tries: int = Field(default=4, ge=1, validation_alias="OFM_STACKED_TRIES")
    retry_wait_s: float = Field(
        default=2.0,
        ge=0,
        validation_alias="OFM_STACKED_RETRY_WAIT_S",
    )
    max_wait_s: float = Field(
        default=65.0,
        ge=0,
        validation_alias="OFM_STACKED_MAX_WAIT_S",
    )
    agency_refresh_s: int = Field(
        default=600,
        ge=60,
        validation_alias="OFM_STACKED_AGENCY_REFRESH_S",
    )
    creator_key_keep_s: int = Field(
        default=604800,
        ge=60,
        validation_alias="OFM_STACKED_CREATOR_KEY_KEEP_S",
    )

    @property
    def api_keys(self) -> list[StackedKey]:
        found = {
            **DotEnvSettingsSource(type(self)).env_vars,
            **EnvSettingsSource(type(self)).env_vars,
        }
        return [
            StackedKey(name=name.removeprefix(_STACKED_KEY_PREFIX), key=key)
            for name, key in sorted(found.items())
            if name.startswith(_STACKED_KEY_PREFIX) and key
        ]


def _names(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


class ReportsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    daily_enabled: bool = Field(
        default=True,
        validation_alias="OFM_REPORTS_DAILY_ENABLED",
    )
    daily_at: str = Field(default="14:00", validation_alias="OFM_REPORTS_DAILY_AT")
    timezone: str = Field(
        default="Europe/Warsaw",
        validation_alias="OFM_REPORTS_TIMEZONE",
    )
    check_s: float = Field(default=60, gt=0, validation_alias="OFM_REPORTS_CHECK_S")

    @field_validator("daily_at")
    @classmethod
    def _hh_mm(cls, value: str) -> str:
        try:
            hour, minute = (int(part) for part in value.split(":"))
            dt.time(hour, minute)
        except ValueError as exc:
            msg = "OFM_REPORTS_DAILY_AT must be HH:MM"
            raise ValueError(msg) from exc
        return value

    def daily_time(self) -> dt.time:
        hour, minute = (int(part) for part in self.daily_at.split(":"))
        return dt.time(hour, minute)


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cpu_sample_s: float = Field(
        default=0.3,
        gt=0,
        le=2,
        validation_alias="OFM_SERVER_CPU_SAMPLE_S",
    )
    warn_percent: int = Field(
        default=85,
        ge=1,
        le=100,
        validation_alias="OFM_SERVER_WARN_PERCENT",
    )
    wait_warn_s: int = Field(
        default=300,
        ge=1,
        validation_alias="OFM_SERVER_WAIT_WARN_S",
    )
    run_warn_s: int = Field(
        default=3600,
        ge=1,
        validation_alias="OFM_SERVER_RUN_WARN_S",
    )
    jobs_shown: int = Field(default=10, ge=1, validation_alias="OFM_SERVER_JOBS_SHOWN")


class EventsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    senders_off: str | None = Field(
        default=None,
        validation_alias="OFM_EVENTS_SENDERS_OFF",
    )
    key_ttl_s: int = Field(
        default=30 * 24 * 3600,
        ge=1,
        validation_alias="OFM_EVENTS_KEY_TTL_S",
    )
    retry_waits_s: list[int] = Field(
        default=[10, 60, 300],
        validation_alias="OFM_EVENTS_RETRY_WAITS_S",
    )
    max_wait_s: float = Field(
        default=60,
        ge=0,
        validation_alias="OFM_EVENTS_MAX_WAIT_S",
    )
    send_timeout_s: int = Field(
        default=180,
        ge=1,
        validation_alias="OFM_EVENTS_SEND_TIMEOUT_S",
    )
    failed_keep_s: int = Field(
        default=14 * 24 * 3600,
        ge=1,
        validation_alias="OFM_EVENTS_FAILED_KEEP_S",
    )

    def senders_off_list(self) -> list[str]:
        return _names(self.senders_off or "")

    def sending(self, production: bool) -> bool:  # noqa: FBT001
        return production if self.senders_off is None else True


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: SecretStr | None = Field(
        default=None,
        validation_alias="OFM_TELEGRAM_BOT_TOKEN",
    )
    chat_ids: str = Field(default="", validation_alias="OFM_TELEGRAM_CHAT_IDS")
    api_url: str = Field(
        default="https://api.telegram.org",
        validation_alias="OFM_TELEGRAM_API_URL",
    )
    timeout_s: float = Field(
        default=15,
        gt=0,
        validation_alias="OFM_TELEGRAM_TIMEOUT_S",
    )
    group_pace_s: float = Field(
        default=3.0,
        ge=0,
        validation_alias="OFM_TELEGRAM_GROUP_PACE_S",
    )
    chat_pace_s: float = Field(
        default=1.0,
        ge=0,
        validation_alias="OFM_TELEGRAM_CHAT_PACE_S",
    )
    ask_wait_s: float = Field(
        default=10,
        ge=1,
        le=25,
        validation_alias="OFM_TELEGRAM_ASK_WAIT_S",
    )
    ask_max_age_s: float = Field(
        default=300,
        gt=0,
        validation_alias="OFM_TELEGRAM_ASK_MAX_AGE_S",
    )
    link_ttl_s: int = Field(
        default=900,
        ge=60,
        validation_alias="OFM_TELEGRAM_LINK_TTL_S",
    )

    def chat_id_list(self) -> list[str]:
        return _names(self.chat_ids)


class PicturesSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    preset_max_images: int = Field(
        default=10,
        ge=1,
        le=20,
        validation_alias="OFM_PICTURES_PRESET_MAX_IMAGES",
    )
    max_pictures: int = Field(
        default=20,
        ge=1,
        le=100,
        validation_alias="OFM_PICTURES_MAX_PICTURES",
    )
    resolution: str = Field(default="2K", validation_alias="OFM_PICTURES_RESOLUTION")
    aspect_ratio: str = Field(
        default="3:4",
        validation_alias="OFM_PICTURES_ASPECT_RATIO",
    )


class ThreadsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    preset: str = Field(default="threads", validation_alias="OFM_THREADS_PRESET")
    ffmpeg_timeout_s: int = Field(
        default=120,
        ge=1,
        validation_alias="OFM_THREADS_FFMPEG_TIMEOUT_S",
    )
    job_timeout_s: int = Field(
        default=1800,
        ge=60,
        validation_alias="OFM_THREADS_JOB_TIMEOUT_S",
    )


class PinterestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    base_url: str = Field(
        default="https://www.pinterest.com",
        validation_alias="OFM_PINTEREST_BASE_URL",
    )
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        ),
        validation_alias="OFM_PINTEREST_USER_AGENT",
    )
    timeout_s: float = Field(
        default=30,
        gt=0,
        validation_alias="OFM_PINTEREST_TIMEOUT_S",
    )
    upload_timeout_s: float = Field(
        default=300,
        gt=0,
        validation_alias="OFM_PINTEREST_UPLOAD_TIMEOUT_S",
    )
    upload_tries: int = Field(
        default=3,
        ge=1,
        validation_alias="OFM_PINTEREST_UPLOAD_TRIES",
    )
    upload_poll_s: float = Field(
        default=3,
        gt=0,
        validation_alias="OFM_PINTEREST_UPLOAD_POLL_S",
    )
    upload_wait_s: float = Field(
        default=600,
        gt=0,
        validation_alias="OFM_PINTEREST_UPLOAD_WAIT_S",
    )


class DistributionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    pins_per_day: int = Field(
        default=15,
        ge=1,
        le=100,
        validation_alias="OFM_DISTRIBUTION_PINS_PER_DAY",
    )
    handle_share: float = Field(
        default=0.5,
        ge=0,
        le=1,
        validation_alias="OFM_DISTRIBUTION_HANDLE_SHARE",
    )
    check_interval_s: float = Field(
        default=300,
        gt=0,
        validation_alias="OFM_DISTRIBUTION_CHECK_INTERVAL_S",
    )
    jitter: float = Field(
        default=0.3,
        ge=0,
        lt=1,
        validation_alias="OFM_DISTRIBUTION_JITTER",
    )
    rate_limit_pause_s: float = Field(
        default=3600,
        gt=0,
        validation_alias="OFM_DISTRIBUTION_RATE_LIMIT_PAUSE_S",
    )
    failures_before_pause: int = Field(
        default=3,
        ge=1,
        validation_alias="OFM_DISTRIBUTION_FAILURES_BEFORE_PAUSE",
    )
    max_attempts: int = Field(
        default=3,
        ge=1,
        validation_alias="OFM_DISTRIBUTION_MAX_ATTEMPTS",
    )
    job_timeout_s: int = Field(
        default=1800,
        ge=60,
        validation_alias="OFM_DISTRIBUTION_JOB_TIMEOUT_S",
    )
    ffmpeg_timeout_s: int = Field(
        default=120,
        ge=1,
        validation_alias="OFM_DISTRIBUTION_FFMPEG_TIMEOUT_S",
    )
    scheduled_pins: bool | None = Field(
        default=None,
        validation_alias="OFM_DISTRIBUTION_SCHEDULED",
    )

    def scheduled(self, production: bool) -> bool:  # noqa: FBT001
        return production if self.scheduled_pins is None else self.scheduled_pins


class IdempotencySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    keep_s: int = Field(
        default=86400,
        ge=3600,
        validation_alias="OFM_IDEMPOTENCY_KEEP_S",
    )
    stale_s: int = Field(default=600, ge=10, validation_alias="OFM_IDEMPOTENCY_STALE_S")
    sweep_s: int = Field(
        default=3600,
        ge=60,
        validation_alias="OFM_IDEMPOTENCY_SWEEP_S",
    )
