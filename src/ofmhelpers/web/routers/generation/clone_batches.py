"""/clone's gallery shape: jobs grouped back into the batches they were
submitted as.

Every job of one submit carries the same `clone_batch` id and its position in
it (`clone_index`), so grouping needs no table of its own -- a batch is just
"the jobs that share an id", rebuilt from the job store on every render.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, computed_field

from ofmhelpers.web.routers.task_helpers import asset_card, strip_asset_hash_prefix

FILES_PREFIX = "/clone/files"


class CloneCard(BaseModel):
    """One video's run inside a batch -- the same data attributes as a
    /generate gallery card, so generation.js polls and rebuilds it as-is."""

    job_id: str
    status: str
    error: str | None
    params: dict[str, Any]
    assets: list[dict]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def params_json(self) -> str:
        return json.dumps(self.params)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def video_name(self) -> str:
        videos = self.params.get("reference_videos") or [""]
        return strip_asset_hash_prefix(Path(videos[0]).name)

    @classmethod
    def from_job(cls, job: dict) -> "CloneCard":
        assets = []
        if job["status"] == "done":
            assets = [
                asset_card(
                    f["name"],
                    idx,
                    f"{FILES_PREFIX}/{job['id']}",
                    remote_url=f.get("remote_url"),
                )
                for idx, f in enumerate(job.get("result") or [])
            ]
        return cls(
            job_id=job["id"],
            status=job["status"],
            error=job.get("error"),
            params=job["params"],
            assets=assets,
        )


class CloneBatch(BaseModel):
    """One submit: a subject image, its settings, and a card per video in the
    order the videos were picked."""

    id: str
    created_at: float
    cards: list[CloneCard]

    @property
    def _first(self) -> dict[str, Any]:
        return self.cards[0].params

    @computed_field  # type: ignore[prop-decorator]
    @property
    def image(self) -> str:
        return self._first["reference_images"][0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prompt(self) -> str:
        return self._first["prompt"]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolution(self) -> str:
        return self._first["resolution"]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hue(self) -> int:
        """A stable colour per batch, so its cards read as one group at a
        glance and keep that colour across reloads."""
        return int(hashlib.sha256(self.id.encode()).hexdigest()[:4], 16) % 360

    @computed_field  # type: ignore[prop-decorator]
    @property
    def recreate_json(self) -> str:
        """The whole batch as the form's fields: Recreate on the group puts
        back the image, every video, and the settings."""
        return json.dumps(
            {
                "prompt": self.prompt,
                "resolution": self.resolution,
                "audio": self._first["audio"],
                "reference_images": [self.image],
                "reference_videos": [
                    v for c in self.cards for v in c.params["reference_videos"]
                ],
            }
        )


def group_batches(jobs: list[dict], page_full: bool) -> list[CloneBatch]:
    """Newest batch first, videos in picked order. When the page of jobs was
    full, its oldest batch may be cut off at the page boundary, so it is
    dropped rather than shown with videos missing."""
    grouped: dict[str, list[dict]] = {}
    for job in jobs:
        grouped.setdefault(job["params"]["clone_batch"], []).append(job)

    batches = [
        CloneBatch(
            id=batch_id,
            created_at=min(j["created_at"] for j in members),
            cards=[
                CloneCard.from_job(j)
                for j in sorted(members, key=lambda j: j["params"]["clone_index"])
            ],
        )
        for batch_id, members in grouped.items()
    ]
    if page_full and len(batches) > 1:
        batches.pop()
    return batches
