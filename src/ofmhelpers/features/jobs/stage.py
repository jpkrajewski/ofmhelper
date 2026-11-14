"""What a running job is doing once the provider has answered: its result
exists (`jobs.preview`, written before the download and the export start),
and the card says what is still being made of it. No column of its own: the
preview already is the fact "generated"."""

from ofmhelpers.media.exports import exports
from ofmhelpers.schemas.persistence import Job, JobStatus

SAVING = "Generated. Saving…"
_ORIGINAL = "original"


def stage_of(job: Job) -> str | None:
    """The line under a running card's hosted result; None before the
    provider answered and once the job ended."""
    if job.status != JobStatus.running or job.preview is None:
        return None
    # Runs from before `export` stored only the `capcut` bool.
    name = job.params.get("export") or ("capcut" if job.params.get("capcut") else None)
    choice = exports().get(str(name)) if name else None
    if choice is None or choice.name == _ORIGINAL:
        return SAVING
    return f"Generated. Encoding: {choice.label}…"
