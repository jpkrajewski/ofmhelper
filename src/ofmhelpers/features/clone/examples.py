# Copyright (C) 2026 jpkrajewski

import argparse
import logging
import shutil
from pathlib import Path

from ofmhelpers.db.clone_examples import store
from ofmhelpers.db.clone_examples.schema import CloneExample, ExampleCard, ExamplePart
from ofmhelpers.db.jobs.store import get_job
from ofmhelpers.features.assets.store import uploads_root
from ofmhelpers.features.previews.render import write_video_thumb
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.log import configure_logging
from ofmhelpers.media.side_by_side import side_by_side

logger = logging.getLogger(__name__)

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
    return [_card(example) for example in store.every()]


def file(example_id: int, part: ExamplePart) -> Path:
    path = _folder(example_id) / _FILES[part]
    if store.get(example_id) is None or not path.is_file():
        raise NotFoundError(_GONE)
    return path


def add(job_id: str, title: str) -> CloneExample:
    job = get_job(job_id)
    if job is None or job.task != TASK or job.status != "done":
        raise NotFoundError(_NOT_A_CLONE)
    result = _on_disk(next((p for p in job.local_paths() if p), None))
    videos = job.params.get("reference_videos") or []
    source = _on_disk(str(videos[0]) if videos else None)
    example = store.add(job.id, title.strip(), str(job.params.get("prompt") or ""))
    folder = _folder(example.id)
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / _FILES[ExamplePart.VIDEO]
    side_by_side(
        source,
        result,
        video,
        height=COMPARE_HEIGHT,
        timeout_s=COMPARE_TIMEOUT_S,
    )
    write_video_thumb(video, folder / _FILES[ExamplePart.POSTER], 2 * FRAME_PX)
    logger.info("clone example %s (%s) made from job %s", example.id, title, job.id)
    return example


def remove(example_id: int) -> None:
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
