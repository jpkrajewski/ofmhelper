from typing import Any

from ofmhelpers.features.clone.run import run_clone
from ofmhelpers.features.clone_links import run as clone_link_run
lazy from ofmhelpers.schemas import ResultFile


def run_clone_link(**params: Any) -> list[ResultFile]:
    return clone_link_run.run_clone_link(run_clone=run_clone, **params)
