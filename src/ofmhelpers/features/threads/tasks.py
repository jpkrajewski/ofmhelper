# Copyright (C) 2026 jpkrajewski

from pathlib import Path

import logfire
from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.config.types import AccountFolder
from ofmhelpers.db.drive_folders.schema import folder_url
from ofmhelpers.db.jobs.store import merge_params
from ofmhelpers.features.storage.client import storage_of
from ofmhelpers.integrations.storage import FolderGoneError
from ofmhelpers.log import get_logger
from ofmhelpers.media.repurpose.photos import write_photos
from ofmhelpers.media.repurpose.photos.models import load_photo_preset
lazy from ofmhelpers.features.storage.models import ReviewPlace
lazy from ofmhelpers.features.storage.service import StorageServiceReview

logger = get_logger(__name__)

TASK = "threads"


class Taken(BaseModel):
    name: str
    at: float
    crop: float
    bits: int


class Pictures(BaseModel):
    folder_url: str
    names: list[str]
    taken: list[Taken] = []


class PicturesRun(BaseModel):
    login_id: int
    key: str
    reel_name: str
    account_folder_id: str
    seed: str = ""
    preset: str = "threads"
    folder_id: str = ""

    @property
    def drawn_by(self) -> str:
        return self.seed or self.key

    def here(self, user_id: int) -> Path:
        reel = Path(settings.infra.uploads_root) / TASK / str(user_id)
        reel = reel / str(self.login_id) / self.key
        return reel / self.seed if self.seed else reel

    @property
    def names(self) -> list[str]:
        count = load_photo_preset(self.preset).pictures
        first = 1000 + int.from_bytes(self.drawn_by.encode()) % (9000 - count)
        return [f"IMG_{first + i:04d}.JPG" for i in range(count)]


def make_pictures(
    *,
    user_id: int,
    login_id: int,
    key: str,
    reel_name: str,
    account_folder_id: str,
    seed: str = "",
    preset: str = "threads",
    folder_id: str = "",
    job_id: str = "",
) -> Pictures:
    run = PicturesRun(
        login_id=login_id,
        key=key,
        reel_name=reel_name,
        account_folder_id=account_folder_id,
        seed=seed,
        preset=preset,
        folder_id=folder_id,
    )
    with logfire.span(
        "threads set {key}",
        key=key,
        login_id=login_id,
        seed=seed,
        preset=preset,
    ):
        here = run.here(user_id)
        review = storage_of(user_id, interactive=False).review
        place = review.target()
        made = _made(here)
        if made is None:
            made = _make(place, here, run)
        target = _folder(review, place, run, job_id)
        there = {f.name for f in place.drive.children(target)}
        for taken in made.taken:
            if taken.name not in there:
                place.drive.upload(target, here / taken.name, taken.name, "image/jpeg")
        return Pictures(
            folder_url=folder_url(target),
            names=[t.name for t in made.taken],
            taken=made.taken,
        )


def _folder(
    review: StorageServiceReview,
    place: ReviewPlace,
    run: PicturesRun,
    job_id: str,
) -> str:
    drive = place.drive
    if run.folder_id:
        try:
            return drive.folder(run.folder_id).id
        except FolderGoneError:
            logger.info(
                "threads: set folder %s gone, making another",
                run.folder_id,
                exc_info=True,
            )
    threads = review.account_subfolder(
        place,
        run.account_folder_id,
        AccountFolder.THREADS,
    )
    used = {f.name for f in drive.children(threads, folders_only=True)}
    stem = Path(run.reel_name).stem
    name = next(
        n
        for n in (stem if i == 1 else f"{stem} ({i})" for i in range(1, 10_000))
        if n not in used
    )
    made = drive.create_folder(threads, name).id
    if job_id:
        merge_params(job_id, {"folder_id": made})
    return made


class _Made(BaseModel):
    taken: list[Taken]


def _made(here: Path) -> _Made | None:
    kept = here / "made.json"
    if not kept.is_file():
        return None
    made = _Made.model_validate_json(kept.read_text(encoding="utf-8"))
    return made if all((here / t.name).is_file() for t in made.taken) else None


def _make(place: ReviewPlace, here: Path, run: PicturesRun) -> _Made:
    got = place.drive.download(run.key)
    source = here / f"source{Path(got.name).suffix}"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(got.content)
    del got
    try:
        written = write_photos(
            source,
            [here / name for name in run.names],
            preset=run.preset,
            seed=run.drawn_by,
            timeout_s=settings.threads.ffmpeg_timeout_s,
        )
    finally:
        source.unlink(missing_ok=True)
    made = _Made(
        taken=[
            Taken(name=p.path.name, at=round(p.at, 3), crop=p.crop, bits=p.bits)
            for p in written
        ],
    )
    (here / "made.json").write_text(made.model_dump_json(), encoding="utf-8")
    return made
