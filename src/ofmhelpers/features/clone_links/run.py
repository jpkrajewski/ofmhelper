import math
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.stores import cookies as login_cookies
from ofmhelpers.db.stores.jobs import get_job, merge_params
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.store import register_generated_asset
from ofmhelpers.features.clone.export import (
    EXPORT_FAILURES,
    export_choice,
    export_results,
)
from ofmhelpers.features.clone.pricing import clip_duration
from ofmhelpers.features.clone.targets import (
    CloneTarget,
    clone_targets,
    clone_values,
)
from ofmhelpers.features.clone_links.review import start_reviews
from ofmhelpers.features.clone_links.stop import stopped
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.integrations.ai_providers.base import (
    Provider,
    get_provider,
    provider_class,
)
from ofmhelpers.integrations.ai_providers.errors import (
    InvalidInputError,
    ProviderAuthError,
    ProviderNoCreditsError,
    ProviderUnavailableError,
)
from ofmhelpers.integrations.downloaders.cookies import cookie_file
from ofmhelpers.integrations.downloaders.media import downloaded
from ofmhelpers.log import get_logger
from ofmhelpers.media.exports import ExportOptions
from ofmhelpers.media.video_probe import duration_s
from ofmhelpers.schemas import Job, ResultFile

logger = get_logger(__name__)

TARGET_LABEL = "Wan 3.0"

STOPPED = "Stopped"
GONE = "This run no longer exists"
COULD_NOT_DOWNLOAD = "Could not download this reel"
NEEDS_LOGIN = "Needs login: upload your cookies on the Cookies page"
NO_VIDEO = "No video at this link"
NOT_KEPT = "The video could not be kept"
UNREADABLE = "The video cannot be read"
REFETCH = "Still generating: this reel is downloaded again on the next try"
NOT_SAVED = "Still generating: the video is saved on the next try"

_LOGIN_WALL = re.compile(r"\blog ?in\b|\bsign in\b|cookies", re.IGNORECASE)


def _owner(job: Job | None) -> int | None:
    actor = job.actor if job else None
    return int(actor) if actor and actor.isdigit() else None


class LinkFetchError(RuntimeError): ...


class LinkStoppedError(RuntimeError): ...


class RunGoneError(RuntimeError): ...


class _Source(BaseModel):
    source_video: str
    source_seconds: float


class _Halt(BaseModel):
    kind: Literal["auth", "no_credits"]
    message: str


def _owner_of(job_id: str) -> int:
    owner = _owner(get_job(job_id))
    if owner is None:
        logger.warning("clone-links %s: no job row or no owner: not run", job_id)
        raise RunGoneError(GONE)
    return owner


def _batch(params: dict[str, Any]) -> str | None:
    batch = params.get("batch")
    return batch if isinstance(batch, str) and batch else None


def _saved_source(params: dict[str, Any]) -> _Source | None:
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
    if tasks.recall(job_id) is None and stopped(batch):
        logger.info("clone-links %s: batch %s stopped: not run", job_id, batch)
        raise LinkStoppedError(STOPPED)


def _halt_key(batch: str) -> str:
    return f"clone-links:halt:{batch}"


def _halt(batch: str | None, exc: ProviderAuthError | ProviderNoCreditsError) -> None:
    if batch is None:
        return
    halt = _Halt(
        kind="auth" if isinstance(exc, ProviderAuthError) else "no_credits",
        message=str(exc),
    )
    set_text(_halt_key(batch), halt.model_dump_json(), settings.clone_links.halt_ttl_s)
    logger.warning("clone-links: batch %s halted: %s", batch, exc)


def _refuse_if_halted(job_id: str, batch: str | None, provider: str) -> None:
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
    raise ProviderNoCreditsError(halt.message, provider=provider_class(provider).label)


def _why_not(error: str, *, has_cookies: bool) -> str:
    if not has_cookies and _LOGIN_WALL.search(error):
        return NEEDS_LOGIN
    return COULD_NOT_DOWNLOAD


def _download(url: str, assets_root: str, user_id: int | None) -> tuple[str, float]:
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
    *,
    run_clone: Callable[..., list[ResultFile]],
    **params: Any,
) -> list[ResultFile]:
    params.pop("reference_videos", None)
    params.pop("duration", None)
    saved = _saved_source(params)
    owner = _owner_of(job_id)
    batch = _batch(params)
    _refuse_if_stopped(job_id, batch)
    paid = tasks.recall(job_id) is not None
    if not paid:
        _refuse_if_halted(job_id, batch, provider)
    target = clone_targets()[provider]
    video, seconds = _source(job_id, link, assets_root, owner, target, saved, paid=paid)
    duration = clip_duration(target.spec, seconds)
    target.spec.check(
        clone_values(
            target, prompt, resolution, duration, audio, reference_images, video
        ).model_dump()
    )
    _refuse_if_stopped(job_id, batch)
    try:
        results = run_clone(
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
