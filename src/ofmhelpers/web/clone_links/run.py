"""One /clone-links run on the worker (`run_clone_link`, also recovery's
re-run through `registry.RESUMABLE`), in order:

1. Refused before anything is downloaded when its job row is gone (nobody
   would see what it paid for), its Generate was stopped (`stop.py`), or an
   earlier run of the same Generate was refused by the account itself
   (wrong key, no credits: `clone-links:halt:<batch>` for
   `settings.clone_links.halt_ttl_s`). A run whose provider request is
   already paid (`tasks.recall`) is never stopped or halted: it resumes.
2. The link's video: downloaded as the job's owner (their cookies.txt,
   read here: never in the queued job; the linked post only; the download's
   own folder removed after), kept in their asset store, refused over the
   provider's limit, and saved on the job (`source_video`,
   `source_seconds`). A paid re-run reuses that file; one that must
   download again and cannot fails resumable (`ProviderUnavailableError`):
   the paid request stays remembered for recovery's next sweep instead of
   being forgotten. The card gets a short fixed reason, the downloader's
   own text goes to the log.
3. The /clone run (`clone._run_clone`: provider, export, mirror). A result
   only hosted (its download failed after the generation was paid) is
   fetched again here, registered and exported like any; still not saved:
   resumable failure, recovery fetches it.
4. A review of its video (`review.start_reviews`, never raises)."""

import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.base import Provider, get_provider, provider_class
from ofmhelpers.aigenproviders.errors import (
    InvalidInputError,
    ProviderAuthError,
    ProviderNoCreditsError,
    ProviderUnavailableError,
)
from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.downloaders.cookies import cookie_file
from ofmhelpers.downloaders.media import downloaded
from ofmhelpers.exports import ExportOptions
from ofmhelpers.log import get_logger
from ofmhelpers.utils.video_probe import duration_s
from ofmhelpers.web.clone_links.review import start_reviews
from ofmhelpers.web.clone_links.stop import stopped
from ofmhelpers.web.routers.generation.clone import (
    CLONE_TARGETS,
    EXPORT_FAILURES,
    CloneTarget,
    _clone_values,
    _run_clone,
    clip_duration,
    export_choice,
    export_results,
)
from ofmhelpers.web.routers.task_helpers import (
    classify_kind,
    register_generated_asset,
)
from ofmhelpers.web.schemas import Job, ResultFile
from ofmhelpers.web.stores import cookies as login_cookies
from ofmhelpers.web.stores.jobs import get_job, merge_params

logger = get_logger(__name__)

# Every CLONE_TARGETS model is Wan 3.0 (its refusal names it).
TARGET_LABEL = "Wan 3.0"

# What a card shows (schemas/errors.py REASONS reads their first words).
STOPPED = "Stopped"
GONE = "This run no longer exists"
COULD_NOT_DOWNLOAD = "Could not download this reel"
NEEDS_LOGIN = "Needs login: upload your cookies on the Cookies page"
NO_VIDEO = "No video at this link"
NOT_KEPT = "The video could not be kept"
UNREADABLE = "The video cannot be read"
# Paid already: recovery's next sweep runs it again, nothing is paid twice.
REFETCH = "Still generating: this reel is downloaded again on the next try"
NOT_SAVED = "Still generating: the video is saved on the next try"

# The downloader's words for a login wall (yt-dlp's Instagram redirect to
# the login page, gallery-dl's "needs a login", YouTube's "Sign in").
_LOGIN_WALL = re.compile(r"\blog ?in\b|\bsign in\b|cookies", re.IGNORECASE)


def _owner(job: Job | None) -> int | None:
    """The user a job belongs to (its actor is their id)."""
    actor = job.actor if job else None
    return int(actor) if actor and actor.isdigit() else None


class LinkFetchError(RuntimeError):
    """The link gave no usable video; the message (short, fixed) says why
    on its card."""


class LinkStoppedError(RuntimeError):
    """Its Generate was stopped before this run paid anything. Not a
    `ProviderError`: nothing is remembered, nothing for recovery."""


class RunGoneError(RuntimeError):
    """The run's job row is gone, or belongs to nobody: nothing is
    downloaded or paid for a run no one can see."""


class _Source(BaseModel):
    """The reel a run downloaded and its length, saved on its job (params)
    for a re-run of its paid request."""

    source_video: str
    source_seconds: float


class _Halt(BaseModel):
    """An account-level refusal (`clone-links:halt:<batch>`): the rest of
    that Generate fails with it at once."""

    kind: Literal["auth", "no_credits"]
    message: str


def _owner_of(job_id: str) -> int:
    owner = _owner(get_job(job_id))
    if owner is None:
        logger.warning("clone-links %s: no job row or no owner: not run", job_id)
        raise RunGoneError(GONE)
    return owner


def _batch(params: dict[str, Any]) -> str | None:
    """The Generate the run belongs to (`/run` stamps one per submit)."""
    batch = params.get("batch")
    return batch if isinstance(batch, str) and batch else None


def _saved_source(params: dict[str, Any]) -> _Source | None:
    """The source an earlier run saved on the job, taken out of `params`
    (it must not reach `_run_clone`); None when there is none."""
    video = params.pop("source_video", None)
    seconds = params.pop("source_seconds", None)
    if video is None or seconds is None:
        return None
    try:
        return _Source(source_video=video, source_seconds=seconds)
    except ValidationError:
        logger.warning("clone-links: saved source %r unreadable", video, exc_info=True)
        return None


def _refuse_if_stopped(job_id: str, batch: str | None) -> None:
    """A run of a stopped Generate fails "Stopped" -- unless its request is
    already paid: that one resumes."""
    if tasks.recall(job_id) is None and stopped(batch):
        logger.info("clone-links %s: batch %s stopped: not run", job_id, batch)
        raise LinkStoppedError(STOPPED)


def _halt_key(batch: str) -> str:
    return f"clone-links:halt:{batch}"


def _halt(batch: str | None, exc: ProviderAuthError | ProviderNoCreditsError) -> None:
    """The rest of `batch` fails with `exc` at once (`_refuse_if_halted`)
    instead of downloading its reels first: nothing runs on that key until
    it is fixed or topped up. Redis down: not kept, each run finds out by
    itself as before."""
    if batch is None:
        return
    halt = _Halt(
        kind="auth" if isinstance(exc, ProviderAuthError) else "no_credits",
        message=str(exc),
    )
    set_text(_halt_key(batch), halt.model_dump_json(), settings.clone_links.halt_ttl_s)
    logger.warning("clone-links: batch %s halted: %s", batch, exc)


def _refuse_if_halted(job_id: str, batch: str | None, provider: str) -> None:
    """The refusal an earlier run of `batch` got, raised again (same class,
    same card reason) before this one downloads anything."""
    if batch is None:
        return
    raw = get_text(_halt_key(batch))
    if raw is None:
        return
    try:
        halt = _Halt.model_validate_json(raw)
    except ValidationError:
        logger.warning("clone-links: halt of batch %s unreadable", batch, exc_info=True)
        return
    logger.info("clone-links %s: batch %s halted (%s)", job_id, batch, halt.kind)
    if halt.kind == "auth":
        raise ProviderAuthError(provider_class(provider).label)
    raise ProviderNoCreditsError(halt.message)


def _why_not(error: str, *, has_cookies: bool) -> str:
    """A failed download's card reason: a login wall when the downloader
    says so and the owner has no cookies uploaded (theirs were tried
    otherwise), else "Could not download"."""
    if not has_cookies and _LOGIN_WALL.search(error):
        return NEEDS_LOGIN
    return COULD_NOT_DOWNLOAD


def _download(url: str, assets_root: str, user_id: int | None) -> tuple[str, float]:
    """The link's own post's first video, downloaded as `user_id`, kept in
    their part of the asset store, and its length. The download's folder is
    removed on the way out, whatever happened: only the kept copy stays."""
    cookies = login_cookies.for_user(user_id)
    with (
        cookie_file(cookies) as cookiefile,
        downloaded(url, cookiefile, single=True) as result,
    ):
        if not result.success:
            logger.warning("clone-links: %s not downloaded: %s", url, result.error)
            reason = _why_not(result.error or "", has_cookies=bool(cookies))
            raise LinkFetchError(reason)
        video = next(
            (p for p in result.output_paths if classify_kind(p.name) == "video"), None
        )
        if video is None:
            logger.info(
                "clone-links: %s has no video (%d files)", url, len(result.output_paths)
            )
            raise LinkFetchError(NO_VIDEO)
        kept = register_generated_asset(video, Path(assets_root))
        if kept is None:
            raise LinkFetchError(NOT_KEPT)
        try:
            seconds = duration_s(kept, settings.web.ffmpeg_timeout_s)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            logger.warning("clone-links: %s unreadable", kept, exc_info=True)
            raise LinkFetchError(UNREADABLE) from exc
    logger.info("clone-links: %s -> %s (%.1f s)", url, kept.name, seconds)
    return str(kept.resolve()), seconds


def _refuse_too_long(seconds: float, target: CloneTarget) -> None:
    """Over the target's limit: refused before anything is paid. The length
    is shown rounded up, so it never reads as the limit itself."""
    if seconds <= target.max_reference_s:
        return
    shown = math.ceil(round(seconds * 10, 6)) / 10
    msg = (
        f"Too long for {TARGET_LABEL}: {shown:.1f} s "
        f"(the most is {target.max_reference_s} s)"
    )
    raise InvalidInputError(msg)


def _source(
    job_id: str,
    link: str,
    assets_root: str,
    owner: int,
    target: CloneTarget,
    saved: _Source | None,
    *,
    paid: bool,
) -> tuple[str, float]:
    """The run's source video and its length. `paid` (a re-run of a
    remembered request): the file its first run saved, without downloading
    or measuring again (both passed before it paid); downloading again and
    failing raises `ProviderUnavailableError` so the request is kept."""
    if paid and saved is not None and Path(saved.source_video).is_file():
        logger.info("clone-links %s resumes on %s", job_id, saved.source_video)
        return saved.source_video, saved.source_seconds
    try:
        video, seconds = _download(link, assets_root, owner)
        _refuse_too_long(seconds, target)
    except Exception as exc:
        if not paid:
            raise
        logger.warning(
            "clone-links %s: paid, but %s not downloaded again",
            job_id,
            link,
            exc_info=True,
        )
        raise ProviderUnavailableError(REFETCH) from exc
    merge_params(
        job_id, _Source(source_video=video, source_seconds=seconds).model_dump()
    )
    return video, seconds


def _hosted_dest(provider: str, job_id: str, url: str, index: int) -> Path:
    ext = Path(urlparse(url).path).suffix
    if not ext[1:].isalnum():
        ext = ".mp4"
    out = Path(settings.aigen.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    return out / f"{provider}_{job_id}_{index}{ext}"


def _fetched(client: Provider, url: str, dest: Path) -> Path:
    """`url` saved as `dest`, tried again after each of
    `settings.aigen.download_retry_waits_s`."""
    last: Exception | None = None
    for attempt, wait in enumerate(
        [*settings.aigen.download_retry_waits_s, None], start=1
    ):
        try:
            return client.download(url, dest)
        except (ProviderUnavailableError, OSError) as exc:
            last = exc
            logger.warning(
                "clone-links: %s not saved (try %d)", url, attempt, exc_info=True
            )
        if wait is not None:
            time.sleep(wait)
    raise ProviderUnavailableError(NOT_SAVED) from last


def _saved_here(
    job_id: str,
    api_key: str,
    assets_root: str,
    provider: str,
    results: list[ResultFile],
    params: dict[str, Any],
) -> list[ResultFile]:
    """Every result as a local file. One only hosted -- `run_ai` keeps a
    paid generation whose download failed that way, and no review can
    check it -- is fetched again, registered and exported as the job asked."""
    if all(r.path is not None or not r.remote_url for r in results):
        return results
    client = get_provider(provider, api_key)
    export = export_choice(params.get("export"), bool(params.get("capcut")))
    options = ExportOptions(mirror=bool(params.get("mirror")))
    root = Path(assets_root)
    saved = []
    for index, result in enumerate(results):
        if result.path is not None or not result.remote_url:
            saved.append(result)
            continue
        url = result.remote_url
        path = _fetched(client, url, _hosted_dest(provider, job_id, url, index))
        register_generated_asset(path, root)
        local = ResultFile(name=path.name, path=str(path), remote_url=url)
        exported = export_results(
            job_id,
            [local],
            export,
            options,
            root,
            failures=int(params.get(EXPORT_FAILURES) or 0),
        )
        logger.info("clone-links %s: hosted result %d saved as %s", job_id, index, path)
        saved.extend(exported)
    return saved


def run_clone_link(
    job_id: str,
    api_key: str,
    assets_root: str,
    link: str,
    provider: str,
    prompt: str,
    resolution: str,
    reference_images: list[str],
    audio: bool = False,
    **params: Any,
) -> list[ResultFile]:
    """One run (worker, and recovery's re-run). See the module docstring."""
    # Runs queued while Check still downloaded carried their video.
    params.pop("reference_videos", None)
    params.pop("duration", None)
    saved = _saved_source(params)
    owner = _owner_of(job_id)
    batch = _batch(params)
    _refuse_if_stopped(job_id, batch)
    paid = tasks.recall(job_id) is not None
    if not paid:
        _refuse_if_halted(job_id, batch, provider)
    target = CLONE_TARGETS[provider]
    video, seconds = _source(job_id, link, assets_root, owner, target, saved, paid=paid)
    duration = clip_duration(target.spec, seconds)
    target.spec.check(
        _clone_values(
            target, prompt, resolution, duration, audio, reference_images, video
        )
    )
    _refuse_if_stopped(job_id, batch)
    try:
        results = _run_clone(
            job_id,
            api_key,
            assets_root,
            provider=provider,
            prompt=prompt,
            resolution=resolution,
            duration=duration,
            reference_images=reference_images,
            reference_videos=[video],
            audio=audio,
            link=link,
            **params,
        )
    except (ProviderAuthError, ProviderNoCreditsError) as exc:
        _halt(batch, exc)
        raise
    results = _saved_here(job_id, api_key, assets_root, provider, results, params)
    start_reviews(job_id, owner, [r.path for r in results])
    return results
