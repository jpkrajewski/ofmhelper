"""How long a /clone run should take, from how long recent ones took.

A run's time grows with the clip it clones (prod, 2026-10-22: Higgsfield
720P waits 50-65 s per second of video, ~55 s median), and differs per
provider and resolution, so history is kept as seconds of waiting per second
of video, keyed by (provider, resolution), over the last `clone_eta_runs`
runs of each.

Some runs hang or wait out a deploy (prod: 100-140 s/s). A sample over
`HANG_FACTOR` x its group's median is dropped, so it neither moves the
estimate nor the "usual worst" a running card is flagged past.
"""

from pathlib import Path
from statistics import median

from ofmhelpers.web.schemas import CloneEta, CloneRate, Job

Key = tuple[str, str]

HANG_FACTOR = 1.6


def took_s(job: Job) -> float | None:
    """Submit to finish. Jobs from before finish times were recorded fall
    back to when their newest result file was written, which is when the
    run finished; None when neither is known."""
    if job.took_s is not None:
        return job.took_s
    if job.status != "done":
        return None
    written = [
        Path(p).stat().st_mtime for p in job.local_paths() if p and Path(p).is_file()
    ]
    return max(written) - job.created_at if written else None


def _key(params: dict) -> Key:
    return params.get("provider") or "", params.get("resolution") or ""


def clone_rates(jobs: list[Job], runs: int) -> dict[Key, CloneRate]:
    """The rate of the newest `runs` finished, timed runs per (provider,
    resolution) in `jobs` (newest first), hung runs left out."""
    samples: dict[Key, list[float]] = {}
    for job in jobs:
        took, duration = took_s(job), job.params.get("duration")
        if job.status != "done" or took is None or not duration:
            continue
        group = samples.setdefault(_key(job.params), [])
        if len(group) < runs:
            group.append(took / duration)
    rates = {}
    for key, values in samples.items():
        normal = [v for v in values if v <= HANG_FACTOR * median(values)]
        rates[key] = CloneRate(median=median(normal), worst=max(normal))
    return rates


def clone_eta(rates: dict[Key, CloneRate], params: dict) -> CloneEta | None:
    """The run's expected and slowest normal seconds, or None with no
    history for its provider and resolution yet."""
    rate = rates.get(_key(params))
    duration = params.get("duration")
    if rate is None or not duration:
        return None
    return CloneEta(
        eta_s=round(rate.median * duration), slow_s=round(rate.worst * duration)
    )
