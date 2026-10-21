"""What a gallery renders for one job, and /clone's batches of them.

Every tool page with a gallery (/generate, /download-assets, /clone,
/character-sheet, /uniqify) shows its recent jobs as cards with the same core:
the job's id, status, error, params and result assets. Their templates and the
polling JS key off those fields, so they are one model, not a dict per router.

Data only: the result's asset cards come in already built (`job_assets` in
`routers/task_helpers`, which owns the file URLs).
"""

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, computed_field

from ofmhelpers.web.schemas.jobs import AssetCard, FailedSource
from ofmhelpers.web.schemas.persistence import Job


class JobCard(BaseModel):
    job_id: str
    task: str
    status: str
    error: str | None
    created_at: float
    params: dict[str, Any]
    assets: list[AssetCard]
    failed_sources: list[FailedSource] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def params_json(self) -> str:
        return json.dumps(self.params)

    @classmethod
    def from_job(
        cls,
        job: Job,
        assets: list[AssetCard],
        failed_sources: list[FailedSource],
        **extra: Any,
    ) -> Self:
        return cls(
            job_id=job.id,
            task=job.task,
            status=job.status,
            error=job.error,
            created_at=job.created_at,
            params=job.params,
            assets=assets,
            failed_sources=failed_sources,
            **extra,
        )


class TaskCard(JobCard):
    """A card in a gallery mixing several tools (/generate, /download-assets):
    which tool ran it, and the router prefix generation.js polls to resume it
    while it is still running."""

    task_label: str
    poll_prefix: str


class CloneCard(JobCard):
    """One video's run inside a /clone batch."""

    video_name: str


class CloneBatch(BaseModel):
    """One /clone submit: the subject images, its settings, and a card per
    video in the order the videos were picked."""

    id: str
    created_at: float
    cards: list[CloneCard]

    @property
    def _first(self) -> dict[str, Any]:
        return self.cards[0].params

    @computed_field  # type: ignore[prop-decorator]
    @property
    def images(self) -> list[str]:
        return self._first["reference_images"]

    @property
    def cover(self) -> str | None:
        """The first subject image still on disk; None when every one has
        gone (deleted in the file manager, or a database copied from another
        server), so the page draws a blank tile instead of a broken image."""
        return next((p for p in self.images if Path(p).is_file()), None)

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
        back every image, every video, and the settings."""
        return json.dumps(
            {
                "prompt": self.prompt,
                "resolution": self.resolution,
                "audio": self._first["audio"],
                "export": self._first.get("export")
                or ("capcut" if self._first.get("capcut") else "original"),
                "mirror": self._first.get("mirror", False),
                "reference_images": self.images,
                "reference_videos": [
                    v for c in self.cards for v in c.params["reference_videos"]
                ],
            }
        )

    @classmethod
    def group(cls, cards: list[CloneCard], page_full: bool) -> list[Self]:
        """Cards (newest first) regrouped into the batches they were submitted
        as: every job of one submit carries the same `clone_batch` id and its
        position in it (`clone_index`), so a batch needs no table of its own.

        When the page of jobs was full, its oldest batch may be cut off at the
        page boundary, so it is dropped rather than shown with videos
        missing."""
        grouped: defaultdict[str, list[CloneCard]] = defaultdict(list)
        for card in cards:
            grouped[card.params["clone_batch"]].append(card)

        batches = [
            cls(
                id=batch_id,
                created_at=min(c.created_at for c in members),
                cards=sorted(members, key=lambda c: c.params["clone_index"]),
            )
            for batch_id, members in grouped.items()
        ]
        if page_full and len(batches) > 1:
            batches.pop()
        return batches
