"""The /threads job: one POSTED reel made into a set of stills written as the
phone's own photos by a photo preset (`media.repurpose.photos`: how many,
which phone, how they are picked and retouched), kept on the server for the
page and uploaded to THREADS/<reel> in the account's Drive folder.
Regenerate makes another set from the same reel with a new seed (other
moments, other crops; owner 2026-11-17).

A set owns its Drive folder: its first run takes the next free name in
THREADS (`<reel>`, `<reel> (2)`, ...) and keeps the folder's id in the job's
params, so a rerun uploads into the same folder and two sets never mix
(review 2026-11-18). Nothing in Drive is ever replaced or deleted.

Free (local ffmpeg + Drive) and the same when one set is run again from its
row (its seed and preset are in the params; the Drive token is read at each
run, never stored): a picture already on disk is not cut again, one already
in the folder is not uploaded again."""

from pathlib import Path

import logfire
from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import merge_params
from ofmhelpers.db.stores.users import refresh_token
from ofmhelpers.features.drive.folders import (
    ReviewPlace,
    account_subfolder,
    review_target,
)
from ofmhelpers.integrations.gdrive import FolderGoneError
from ofmhelpers.log import get_logger
from ofmhelpers.media.repurpose.photos import write_photos
from ofmhelpers.media.repurpose.photos.models import load_photo_preset
from ofmhelpers.schemas.gdrive import AccountFolder, folder_url

logger = get_logger(__name__)

TASK = "threads"


class Taken(BaseModel):
    """One picture of a set as the job row keeps it: its name, the moment of
    the reel it is from, the crop it took and how far (PDQ bits) it is from
    the nearest still or picture before it."""

    name: str
    at: float
    crop: float
    bits: int


class Pictures(BaseModel):
    """A done job's result: where the set is in Drive, and its pictures."""

    folder_url: str
    names: list[str]
    taken: list[Taken] = []


class PicturesRun(BaseModel):
    """One set of one reel: what its job runs with besides its person (the
    job's actor), all of it in the job's params, so recovery runs it again
    from the row."""

    login_id: int
    key: str
    reel_name: str
    account_folder_id: str
    # Drives everything random in the set; "" = the reel's key (the first
    # sets, made before Regenerate).
    seed: str = ""
    # The photo preset (`media/repurpose/photos/<name>.json`), kept so a
    # rerun makes the same set even after the setting changed.
    preset: str = "threads"
    # The set's own Drive folder, written by its first run.
    folder_id: str = ""

    @property
    def drawn_by(self) -> str:
        return self.seed or self.key

    def here(self, user_id: int) -> Path:
        """Where the set's pictures are kept on the server."""
        reel = Path(settings.infra.uploads_root) / TASK / str(user_id)
        reel = reel / str(self.login_id) / self.key
        return reel / self.seed if self.seed else reel

    @property
    def names(self) -> list[str]:
        """The camera's numbering for the set: consecutive, the same every
        run of it (a rerun finds what it made)."""
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
    """A set of reel `key` (its Drive file id) of the account's POSTED, in
    its own folder in THREADS. Raises `DriveError`, `StillError`."""
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
        "threads set {key}", key=key, login_id=login_id, seed=seed, preset=preset
    ):
        here = run.here(user_id)
        place = review_target(refresh_token(user_id), user_id)
        made = _made(here)
        if made is None:
            made = _make(place, here, run)
        target = _folder(place, run, job_id)
        there = {f.name for f in place.drive.children(target)}
        for taken in made.taken:
            if taken.name not in there:
                place.drive.upload(target, here / taken.name, taken.name, "image/jpeg")
        return Pictures(
            folder_url=folder_url(target),
            names=[t.name for t in made.taken],
            taken=made.taken,
        )


def _folder(place: ReviewPlace, run: PicturesRun, job_id: str) -> str:
    """The set's own folder: the one its first run made (kept in the job's
    params), else the next free name in THREADS, made and kept now."""
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
    threads = account_subfolder(place, run.account_folder_id, AccountFolder.THREADS)
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
    """The pictures a set gave (`made.json` next to them): written last, so
    a run cut off before it makes them again."""

    taken: list[Taken]


def _made(here: Path) -> _Made | None:
    kept = here / "made.json"
    if not kept.is_file():
        return None
    made = _Made.model_validate_json(kept.read_text(encoding="utf-8"))
    return made if all((here / t.name).is_file() for t in made.taken) else None


def _make(place: ReviewPlace, here: Path, run: PicturesRun) -> _Made:
    """The reel downloaded, the set's pictures written (as many different
    ones as it has, up to the preset's `pictures`), the reel deleted
    again."""
    got = place.drive.download(run.key)
    source = here / f"source{Path(got.name).suffix}"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(got.content)
    # The reel's bytes (tens of MB) need not stay in memory while it is cut.
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
        ]
    )
    (here / "made.json").write_text(made.model_dump_json(), encoding="utf-8")
    return made
