# Copyright (C) 2026 jpkrajewski

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.db.downloads.schema import DownloadsQueued
from ofmhelpers.db.jobs.store import create_job
from ofmhelpers.features.downloads.again import Again
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import RefusedError, input_refused
from ofmhelpers.integrations.downloaders.urls import read_links


class LinkDownloads(BaseModel, frozen=True):
    task: str
    run: Callable[..., Any]
    actor: str
    owner_id: int
    assets_root: Path
    again: Again

    def start(self, pasted: str) -> DownloadsQueued:
        with input_refused():
            links = list(dict.fromkeys(read_links(pasted)))
        if not links:
            msg = "At least one URL is required"
            raise RefusedError(msg)
        seen = self.again.sort_out(links)
        if not seen.new:
            return DownloadsQueued(again=seen.jobs)
        job_id = self.queue(seen.new)
        return DownloadsQueued(job_id=job_id, job_ids=[job_id], again=seen.jobs)

    def queue(self, links: list[str], noted: dict[str, str] | None = None) -> str:
        job_id = create_job(
            self.task,
            {"urls": links, **(noted or {})},
            actor=self.actor,
        )
        enqueue(
            run_job,
            (
                job_id,
                self.run,
                {
                    "urls": links,
                    "assets_root": str(self.assets_root),
                    "user_id": self.owner_id,
                    "job_id": job_id,
                },
            ),
        )
        return job_id
