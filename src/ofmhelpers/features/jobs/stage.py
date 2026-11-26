# Copyright (C) 2026 jpkrajewski

from ofmhelpers.db.jobs.schema import Job, JobStatus
from ofmhelpers.media.exports import exports

SAVING = "Generated. Saving…"
_ORIGINAL = "original"


def stage_of(job: Job) -> str | None:
    if job.status != JobStatus.running or job.preview is None:
        return None
    name = job.params.get("export") or ("capcut" if job.params.get("capcut") else None)
    choice = exports().get(str(name)) if name else None
    if choice is None or choice.name == _ORIGINAL:
        return SAVING
    return f"Generated. Encoding: {choice.label}…"
