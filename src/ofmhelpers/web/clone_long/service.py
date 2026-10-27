"""A long clone: one subject image + one video of any length (up to
`settings.clone_long.max_video_s`) through Wan 3.0, which takes at most
15 s. The video is split (`Plan`), every piece generated with the same
image and prompt -- in parallel, each as its own remembered request, so a
re-run resumes every piece and never pays for one twice -- and the results
joined into one video of exactly the source's aspect ratio: every piece is
asked at Wan's nearest ratio (16:9 in: "16:9"), the join fits them to the
source's own. No export, no audio.

A run's files wait in its work folder (`work_dir() / job_id`): each cut
piece and each generated piece as it is saved, so a re-run neither cuts nor
downloads a piece twice. Generated pieces are never results of their own
(not in the asset store, not in the AI output folder): only the joined
video is. The folder goes once the video is joined, or when the run fails
in a way nothing will resume (`stores.jobs.resumable`); one left by a
cut-off run nobody resumed goes after `resume_max_age_s`.
"""

from __future__ import annotations

import contextvars
import math
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict
from rq.timeouts import JobTimeoutException

from ofmhelpers.aigenproviders import runner, tasks
from ofmhelpers.aigenproviders.base import get_provider
from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.aigenproviders.errors import ProviderGenerationError
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web.clone_long.models import (
    LongCloneParams,
    Plan,
    Quote,
    Rate,
    Shape,
)
from ofmhelpers.web.clone_long.video import VideoTools
from ofmhelpers.web.schemas import JobPreview, ResultFile
from ofmhelpers.web.stores.jobs import resumable, set_job_preview

if TYPE_CHECKING:
    from ofmhelpers.aigenproviders.spec import ModelSpec

logger = get_logger(__name__)


class Target(BaseModel):
    """One provider's Wan 3.0 reference-to-video: its catalog model and the
    schema fields the image, the video and the audio switch go in."""

    model_config = ConfigDict(frozen=True)

    model: str
    image_field: str
    video_field: str
    audio_field: str
    max_piece_s: int = 15

    @property
    def spec(self) -> ModelSpec:
        return get_spec(self.model)

    def duration(self, seconds: float) -> int:
        """`seconds` as the nearest whole duration the model accepts."""
        field = self.spec.properties["duration"]
        low = int(field.minimum) if field.minimum is not None else 1
        high = int(field.maximum) if field.maximum is not None else self.max_piece_s
        return min(max(round(seconds), low), high)

    def aspect_ratio(self, shape: Shape) -> str:
        """The model's ratio nearest `shape`, the same for every piece
        ("adaptive" could pick a different one per piece)."""
        allowed = self.spec.properties["aspect_ratio"].enum or []
        return shape.nearest([str(r) for r in allowed if ":" in str(r)])

    def values(
        self,
        params: LongCloneParams,
        seconds: float,
        video: str,
        chain_image: str | None = None,
    ) -> dict[str, Any]:
        """One piece's request, in this model's own field names. `chain_image`
        (the previous piece's last frame) is added as a second reference image
        with a 'start from it' prompt (`settings.clone_long.chain`)."""
        allowed = self.spec.properties["resolution"].enum or []
        resolution = next(
            (str(v) for v in allowed if str(v).lower() == params.resolution.lower()),
            params.resolution,
        )
        images = [params.image]
        prompt = params.prompt
        if chain_image is not None:
            images.append(chain_image)
            prompt = f"{prompt} Start video from @Image2"
        return {
            "prompt": prompt,
            "resolution": resolution,
            "aspect_ratio": self.aspect_ratio(params.shape),
            "duration": self.duration(seconds),
            self.audio_field: settings.clone_long.audio,
            self.image_field: images,
            self.video_field: [video],
        }


# A provider joins by one entry.
TARGETS = {
    "kie": Target(
        model="kie:wan/3-0-video",
        image_field="reference_image_urls",
        video_field="reference_video_urls",
        audio_field="audio",
    ),
    "higgsfield": Target(
        model="higgsfield:alibaba/wan-3.0/reference-to-video",
        image_field="image_urls",
        video_field="video_urls",
        audio_field="generate_audio",
    ),
}

RESOLUTIONS = ("480P", "720P", "1080P")


def rate(provider: str) -> Rate:
    """The provider's Wan 3.0 price (the same rates /clone shows)."""
    web = settings.web
    if provider == "higgsfield":
        return Rate(
            credits_per_s=web.clone_hf_credits_per_video_s,
            usd_per_credit=web.hf_usd_per_credit,
        )
    return Rate(
        credits_per_s=web.clone_credits_per_video_s,
        usd_per_credit=web.kie_usd_per_credit,
    )


def work_dir() -> Path:
    """Where runs' pieces wait, one folder per job (see the module doc)."""
    return Path(settings.infra.uploads_root) / ".clone-long"


def prune_work(keep: str) -> None:
    """Drop the work folders untouched for longer than a request is
    remembered (their runs can no longer resume), except `keep`'s."""
    root = work_dir()
    if not root.is_dir():
        return
    oldest = time.time() - settings.aigen.resume_max_age_s
    for folder in root.iterdir():
        if folder.name != keep and folder.is_dir() and folder.stat().st_mtime < oldest:
            shutil.rmtree(folder, ignore_errors=True)
            logger.info("clone-long: dropped the stale work folder %s", folder.name)


class _NotStartedError(Exception):
    """A piece left out: an earlier one failed."""


def _hosted(urls: list[str]) -> list[str]:
    """Result URLs a browser can open (the fake provider's are not)."""
    return [u for u in urls if u.startswith(("https://", "http://"))]


class LongClone:
    """One run: its plan, its price, its time limit, and the run itself."""

    def __init__(self, params: LongCloneParams) -> None:
        self.params = params
        self.target = TARGETS[params.provider]
        self.plan = Plan.split(params.seconds, self.target.max_piece_s)
        self.tools = VideoTools(settings.clone_long.ffmpeg_timeout_s)

    @classmethod
    def prepare(
        cls,
        *,
        provider: str,
        prompt: str,
        resolution: str,
        image: str,
        video: str,
        seconds: float,
        shape: Shape,
    ) -> LongClone:
        """A run priced and checked against the model piece by piece before
        anything is paid. Raises `InvalidInputError`."""
        target = TARGETS[provider]
        plan = Plan.split(seconds, target.max_piece_s)
        quote = rate(provider).quote(seconds, resolution, plan.count)
        clone = cls(
            LongCloneParams(
                provider=provider,
                model=target.model,
                prompt=prompt,
                resolution=resolution,
                image=image,
                video=video,
                width=shape.width,
                height=shape.height,
                seconds=seconds,
                pieces=plan.count,
                credits=quote.credits,
                usd=quote.usd,
            )
        )
        for piece in plan.pieces:
            target.spec.check(target.values(clone.params, piece.length, video))
        return clone

    @property
    def quote(self) -> Quote:
        p = self.params
        return Quote(seconds=p.seconds, pieces=p.pieces, credits=p.credits, usd=p.usd)

    def time_limit(self) -> int:
        """RQ's limit: the queue's default (one generation and its download)
        plus a provider wait per extra round of parallel pieces, and an
        ffmpeg limit per round's cuts and for the join."""
        cfg = settings.clone_long
        rounds = math.ceil(self.plan.count / cfg.parallel_pieces)
        return (
            settings.infra.rq_job_timeout_s
            + (rounds - 1) * settings.aigen.video_poll_timeout_s
            + (rounds + 1) * cfg.ffmpeg_timeout_s
        )

    def run(self, job_id: str, api_key: str, assets_root: str) -> list[ResultFile]:
        """Cut, generate every piece, join. A piece refused for good (flagged,
        failed) fails the run and every piece is forgotten -- else recovery
        would re-run the job and pay for the refused one again. A piece
        still generating (timeout) stays remembered for recovery, and so do
        the saved pieces."""
        # Imported here: the routers package (which registers this tool)
        # imports this module.
        from ofmhelpers.web.routers.task_helpers import register_generated_asset

        prune_work(keep=job_id)
        work = work_dir() / job_id
        out = Path(settings.aigen.out_dir) / f"clone_long_{job_id}.mp4"
        try:
            self.tools.join(
                self._generate(job_id, api_key, work),
                out,
                self.params.shape,
                colour_match=settings.clone_long.colour_match,
                audio=settings.clone_long.audio,
            )
        except Exception as exc:
            refused = isinstance(exc, ProviderGenerationError)
            if refused:
                tasks.forget(job_id)
            # RQ's time limit lands mid-run: pieces may still be using the
            # folder. Otherwise every piece has ended, and the folder is kept
            # only for recovery, which needs a remembered request (a wrong
            # key fails before any is made).
            keep = isinstance(exc, JobTimeoutException) or (
                not refused and resumable(exc) and self._remembered(job_id)
            )
            if not keep:
                shutil.rmtree(work, ignore_errors=True)
            raise
        register_generated_asset(out, Path(assets_root))
        shutil.rmtree(work, ignore_errors=True)
        logger.info("clone-long %s: %d pieces joined", job_id, self.plan.count)
        return [ResultFile(name=out.name, path=str(out))]

    def _remembered(self, job_id: str) -> bool:
        return any(tasks.recall(job_id, part) for part in range(self.plan.count))

    def _generate(self, job_id: str, api_key: str, work: Path) -> list[Path]:
        """Every piece, `parallel_pieces` at a time, in order. The first
        failure starts no further piece (none is paid for after a refusal or
        an empty balance); the ones already generating are waited for. A
        refusal is raised before any other error. RQ ending the job meanwhile
        (its time limit) leaves those to the work horse's exit, and their
        requests to recovery."""
        if settings.clone_long.chain:
            return self._generate_chained(job_id, api_key, work)
        # Set by the first failure; checked before a piece does anything (a
        # pool thread takes the next piece the moment its own one fails).
        stop = threading.Event()

        def piece(part: int) -> Path:
            if stop.is_set():
                raise _NotStartedError
            try:
                return self._piece(job_id, api_key, work, part)
            except BaseException:
                stop.set()
                raise

        workers = min(settings.clone_long.parallel_pieces, self.plan.count)
        pool = ThreadPoolExecutor(workers)
        try:
            # Each piece in its own copy of this context: its spans and logs
            # stay in the job's trace.
            futures = [
                pool.submit(contextvars.copy_context().run, piece, part)
                for part in range(self.plan.count)
            ]
            wait(futures)
        finally:
            stop.set()
            pool.shutdown(wait=False, cancel_futures=True)
        errors = [
            e
            for f in futures
            if (e := f.exception()) is not None and not isinstance(e, _NotStartedError)
        ]
        refused = [e for e in errors if isinstance(e, ProviderGenerationError)]
        if errors:
            raise (refused or errors)[0]
        return [f.result() for f in futures]

    def _generate_chained(self, job_id: str, api_key: str, work: Path) -> list[Path]:
        """Pieces in order, each started from the previous piece's last frame
        (`settings.clone_long.chain`): the subject carries across the seam.
        No parallelism -- piece N needs piece N-1's output. A failure raises
        at once, so no later piece is paid for; saved pieces (a re-run) are
        taken as they are and still give the next piece its start frame."""
        results: list[Path] = []
        chain_image: Path | None = None
        for part in range(self.plan.count):
            results.append(self._piece(job_id, api_key, work, part, chain_image))
            chain_image = self.tools.last_frame(results[-1], work / f"chain_{part}.png")
        return results

    def _piece(
        self,
        job_id: str,
        api_key: str,
        work: Path,
        part: int,
        chain_image: Path | None = None,
    ) -> Path:
        """Piece `part` generated and saved in `work`; one saved by an
        earlier run of this job is taken as it is, nothing asked again. The
        first piece is the card's preview while the rest generate.
        `chain_image`: the previous piece's last frame, when chaining."""
        saved = work / f"result_{part}.mp4"
        if saved.is_file():
            return saved
        p = self.params
        piece = self.plan.pieces[part]
        cut = work / f"piece_{part}.mp4"
        if not cut.is_file():
            limit = settings.clone_long.piece_max_side
            self.tools.cut(Path(p.video), piece, cut, p.shape.within(limit))
        spec = self.target.spec
        client = get_provider(p.provider, api_key)
        chain = str(chain_image) if chain_image is not None else None
        payload = spec.request(
            self.target.values(p, piece.length, str(cut), chain), client.upload
        )

        def preview(urls: list[str]) -> None:
            if hosted := _hosted(urls):
                set_job_preview(job_id, JobPreview(remote_url=hosted[0], kind="video"))

        first, *extra = runner.generate(
            client, spec, payload, job_id, preview if part == 0 else None, part=part
        )
        for path in extra:
            path.unlink(missing_ok=True)
        # Moved, not linked: the AI output folder keeps only results. Via a
        # temp name, so a move cut short is never taken as saved.
        moving = saved.with_suffix(".part.mp4")
        shutil.move(first, moving)
        moving.replace(saved)
        return saved


def run_long_clone(
    job_id: str, api_key: str, assets_root: str, **params: Any
) -> list[ResultFile]:
    """The worker's entry (and recovery's re-run): the job's stored params."""
    run = LongClone(LongCloneParams.model_validate(params))
    return run.run(job_id, api_key, assets_root)


def time_limit(params: dict[str, Any]) -> int:
    """`Tool.job_timeout` for a stored job."""
    return LongClone(LongCloneParams.model_validate(params)).time_limit()
