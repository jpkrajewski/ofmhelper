# Copyright (C) 2026 jpkrajewski

import contextvars
import math
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_serializer
from rq.timeouts import JobTimeoutException

from ofmhelpers.config import settings
from ofmhelpers.db.jobs.schema import JobPreview, ResultFile
from ofmhelpers.db.jobs.store import set_job_preview
from ofmhelpers.features.assets.store import register_generated_asset
from ofmhelpers.features.clone_long.models import (
    LongCloneParams,
    Plan,
    Quote,
    Rate,
    Shape,
)
from ofmhelpers.features.clone_long.video import VideoTools
from ofmhelpers.features.clone_pricing.pricing import clone_price
from ofmhelpers.features.clone_pricing.targets import (
    CloneTarget,
    clone_targets,
    offered_resolutions,
)
from ofmhelpers.features.jobs.runner import resumable
from ofmhelpers.features.refusals import input_refused
from ofmhelpers.integrations.ai_providers import runner, tasks
from ofmhelpers.integrations.ai_providers.base import get_provider, uploader
from ofmhelpers.integrations.ai_providers.errors import ProviderGenerationError
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class Target(CloneTarget):
    @property
    def image_field(self) -> str:
        return self.images

    @property
    def video_field(self) -> str:
        return self.video

    @property
    def audio_field(self) -> str:
        return self.audio

    def duration(self, seconds: float) -> int:
        field = self.spec.properties["duration"]
        low = int(field.minimum) if field.minimum is not None else 1
        high = int(field.maximum) if field.maximum is not None else self.max_reference_s
        return min(max(round(seconds), low), high)

    def aspect_ratio(self, shape: Shape) -> str:
        allowed = self.spec.properties["aspect_ratio"].enum or []
        return shape.nearest([str(r) for r in allowed if ":" in str(r)])

    def values(
        self,
        params: LongCloneParams,
        seconds: float,
        video: str,
        chain_image: str | None = None,
    ) -> PieceValues:
        images = [params.image]
        prompt = params.prompt
        if chain_image is not None:
            images.append(chain_image)
            prompt = f"{prompt} Start video from @Image2"
        return PieceValues(
            target=self,
            prompt=prompt,
            resolution=self.resolution_value(params.resolution),
            aspect_ratio=self.aspect_ratio(params.shape),
            duration=self.duration(seconds),
            audio=params.audio,
            images=images,
            video=video,
        )


class PieceValues(BaseModel):
    target: Target
    prompt: str
    resolution: str
    aspect_ratio: str
    duration: int
    audio: bool
    images: list[str]
    video: str

    @model_serializer
    def _in_the_models_names(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "resolution": self.resolution,
            "aspect_ratio": self.aspect_ratio,
            "duration": self.duration,
            self.target.audio: self.audio,
            self.target.images: self.images,
            self.target.video: [self.video],
        }


TARGETS = {
    name: Target(**row.model_dump())
    for name, row in clone_targets().items()
    if row.long_clone
}

RESOLUTIONS = offered_resolutions(TARGETS.values())


def rate(provider: str) -> Rate:
    price = clone_price(provider)
    return Rate(
        credits_per_s=price.credits_per_video_s,
        usd_per_credit=price.usd_per_credit,
    )


def work_dir() -> Path:
    return Path(settings.infra.uploads_root) / ".clone-long"


def prune_work(keep: str) -> None:
    root = work_dir()
    if not root.is_dir():
        return
    oldest = time.time() - settings.aigen.resume_max_age_s
    for folder in root.iterdir():
        if folder.name != keep and folder.is_dir() and folder.stat().st_mtime < oldest:
            shutil.rmtree(folder, ignore_errors=True)
            logger.info("clone-long: dropped the stale work folder %s", folder.name)


class _NotStartedError(Exception): ...


def _hosted(urls: list[str]) -> list[str]:
    return [u for u in urls if u.startswith(("https://", "http://"))]


class LongClone:
    def __init__(self, params: LongCloneParams) -> None:
        self.params = params
        self.target = TARGETS[params.provider]
        self.plan = Plan.split(params.seconds, self.target.max_reference_s)
        self.tools = VideoTools(settings.clone_long.ffmpeg_timeout_s)

    @classmethod
    def prepare(  # noqa: PLR0913
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
        target = TARGETS[provider]
        plan = Plan.split(seconds, target.max_reference_s)
        quote = rate(provider).quote(seconds, resolution, plan.count)
        switches = settings.clone_long
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
                chain=switches.chain,
                colour_match=switches.colour_match,
                audio=switches.audio,
            ),
        )
        with input_refused():
            for piece in plan.pieces:
                target.spec.check(
                    target.values(clone.params, piece.length, video).model_dump(),
                )
        return clone

    @property
    def quote(self) -> Quote:
        p = self.params
        return Quote(seconds=p.seconds, pieces=p.pieces, credits=p.credits, usd=p.usd)

    def time_limit(self) -> int:
        cfg = settings.clone_long
        rounds = (
            self.plan.count
            if self.params.chain
            else math.ceil(self.plan.count / cfg.parallel_pieces)
        )
        return (
            settings.infra.rq_job_timeout_s
            + (rounds - 1) * settings.aigen.video_poll_timeout_s
            + (rounds + 1) * cfg.ffmpeg_timeout_s
        )

    def run(self, job_id: str, api_key: str, assets_root: str) -> list[ResultFile]:
        prune_work(keep=job_id)
        work = work_dir() / job_id
        out = Path(settings.aigen.out_dir) / f"clone_long_{job_id}.mp4"
        try:
            self.tools.join(
                self._generate(job_id, api_key, work),
                out,
                self.params.shape,
                work=work,
                colour_match=self.params.colour_match,
                audio=self.params.audio,
            )
        except Exception as exc:
            refused = isinstance(exc, ProviderGenerationError)
            if refused:
                tasks.forget(job_id)
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
        if self.params.chain:
            return self._generate_chained(job_id, api_key, work)
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
            self.target.values(p, piece.length, str(cut), chain).model_dump(),
            uploader(client, spec),
        ).model_dump()

        def preview(urls: list[str]) -> None:
            if hosted := _hosted(urls):
                set_job_preview(job_id, JobPreview(remote_url=hosted[0], kind="video"))

        first, *extra = runner.generate(
            client,
            spec,
            payload,
            job_id,
            preview if part == 0 else None,
            part=part,
        )
        for path in extra:
            path.unlink(missing_ok=True)
        moving = saved.with_suffix(".part.mp4")
        first.move(moving)
        moving.replace(saved)
        return saved


def run_long_clone(  # noqa: PLR0913, PLR0917
    job_id: str,
    api_key: str,
    assets_root: str,
    provider: str,
    model: str,
    prompt: str,
    resolution: str,
    image: str,
    video: str,
    width: int,
    height: int,
    seconds: float,
    pieces: int,
    credits: float,  # noqa: A002
    usd: float,
    chain: bool = False,  # noqa: FBT001, FBT002
    colour_match: bool = False,  # noqa: FBT001, FBT002
    audio: bool = False,  # noqa: FBT001, FBT002
) -> list[ResultFile]:
    params = LongCloneParams(
        provider=provider,
        model=model,
        prompt=prompt,
        resolution=resolution,
        image=image,
        video=video,
        width=width,
        height=height,
        seconds=seconds,
        pieces=pieces,
        credits=credits,
        usd=usd,
        chain=chain,
        colour_match=colour_match,
        audio=audio,
    )
    return LongClone(params).run(job_id, api_key, assets_root)


def time_limit(params: dict[str, Any]) -> int:
    return LongClone(LongCloneParams.model_validate(params)).time_limit()
