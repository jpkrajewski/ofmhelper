"""A /clone-links run as the queue names it: `run.run_clone_link` with the
/clone run it is made with. What Generate queues and recovery re-runs
(`features/jobs/registry.py` `RESUMABLE`). `run_clone` is looked up here when
called."""

from typing import Any

from ofmhelpers.features.clone.run import run_clone
from ofmhelpers.features.clone_links import run as clone_link_run
lazy from ofmhelpers.schemas import ResultFile


def run_clone_link(**params: Any) -> list[ResultFile]:
    return clone_link_run.run_clone_link(run_clone=run_clone, **params)
