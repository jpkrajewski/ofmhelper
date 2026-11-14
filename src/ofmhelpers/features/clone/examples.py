"""/clone's SOP: a fixed handful of examples -- one video with the original
on the left and its clone on the right, and the prompt that made it -- shown to everyone who may
open /clone, so a new VA copies a prompt that works (owner 2026-11-13).

Fixed: nothing on the page adds, renames or removes one (owner 2026-11-14:
"this should not be dynamic, set once"). Whoever runs the server sets them,
in the app container:

    python -m ofmhelpers.features.clone.examples add <job_id> "<title>"
    python -m ofmhelpers.features.clone.examples remove <example_id>

An example is kept for good: when added it copies what it shows out of the
run -- the prompt into its row, the side-by-side video and its poster into
`<uploads>/sop/<id>/` -- because a person's old runs are deleted past
`settings.web.max_jobs` and source videos are swept from the asset store.
It no longer needs its run from then on.

Published on purpose, so this is the one place a person sees something of
another person's run. The subject's own picture (the run's reference image)
is never copied or shown.
"""

import argparse
import shutil
from pathlib import Path

from ofmhelpers.db.stores import clone_examples as store
from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.features.assets.store import uploads_root
from ofmhelpers.features.previews.render import write_video_thumb
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.log import configure_logging, get_logger
from ofmhelpers.media.side_by_side import side_by_side
from ofmhelpers.schemas.clone_examples import CloneExample, ExampleCard, ExamplePart

logger = get_logger(__name__)

TASK = "clone"
FRAME_PX = 480
COMPARE_HEIGHT = 720
COMPARE_TIMEOUT_S = 300
_FILES = {
    ExamplePart.VIDEO: "compare.mp4",
    ExamplePart.POSTER: "poster.webp",
}
_GONE = "No such example"
_NOT_A_CLONE = "Not a finished clone with its video and source on this server"


def examples_dir() -> Path:
    return uploads_root() / "sop"


def _folder(example_id: int) -> Path:
    return examples_dir() / str(example_id)


def _on_disk(path: str | None) -> Path:
    """A file a run names, still on this server."""
    found = Path(path) if path else None
    if found is None or not found.is_file():
        raise NotFoundError(_NOT_A_CLONE)
    return found


def _card(example: CloneExample) -> ExampleCard:
    base = f"/clone/examples/{example.id}"
    return ExampleCard(
        id=example.id,
        title=example.title,
        prompt=example.prompt,
        video_url=f"{base}/{ExamplePart.VIDEO}",
        poster_url=f"{base}/{ExamplePart.POSTER}",
    )


def shown() -> list[ExampleCard]:
    """Every example, in the page's order."""
    return [_card(example) for example in store.every()]


def file(example_id: int, part: ExamplePart) -> Path:
    """One of an example's own files."""
    path = _folder(example_id) / _FILES[part]
    if store.get(example_id) is None or not path.is_file():
        raise NotFoundError(_GONE)
    return path


def add(job_id: str, title: str) -> CloneExample:
    """A finished clone as an example: its prompt in the row, its source
    and result made into one side-by-side video in the example's own folder. Checked
    before anything is written; the same run added again is the same
    example, its files copied afresh."""
    job = get_job(job_id)
    if job is None or job.task != TASK or job.status != "done":
        raise NotFoundError(_NOT_A_CLONE)
    result = _on_disk(next((p for p in job.local_paths() if p), None))
    videos = job.params.get("reference_videos") or []
    source = _on_disk(str(videos[0]) if videos else None)
    example = store.add(job.id, title.strip(), str(job.params.get("prompt") or ""))
    folder = _folder(example.id)
    folder.mkdir(parents=True, exist_ok=True)
    # One video, the original on the left and the clone on the right, in
    # step (owner 2026-11-14: "combine og and cloned"). H.264 8 bit: a
    # phone-recording export is HEVC 10-bit HDR, which Chrome does not play.
    video = folder / _FILES[ExamplePart.VIDEO]
    side_by_side(
        source, result, video, height=COMPARE_HEIGHT, timeout_s=COMPARE_TIMEOUT_S
    )
    write_video_thumb(video, folder / _FILES[ExamplePart.POSTER], 2 * FRAME_PX)
    logger.info("clone example %s (%s) made from job %s", example.id, title, job.id)
    return example


def remove(example_id: int) -> None:
    """The row and its folder; asked twice, gone once."""
    if store.remove(example_id):
        shutil.rmtree(_folder(example_id), ignore_errors=True)
        logger.info("clone example %s removed", example_id)


def _main() -> None:
    parser = argparse.ArgumentParser(description="The examples of /clone's SOP.")
    commands = parser.add_subparsers(dest="command", required=True)
    adding = commands.add_parser("add", help="make a finished clone an example")
    adding.add_argument("job_id")
    adding.add_argument("title")
    removing = commands.add_parser("remove", help="take an example out")
    removing.add_argument("example_id", type=int)
    args = parser.parse_args()
    configure_logging()
    if args.command == "add":
        print(add(args.job_id, args.title).model_dump_json())
    else:
        remove(args.example_id)


if __name__ == "__main__":
    _main()
