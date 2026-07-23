"""
No Redis, no SQLite, no queue. Just a dict in memory.
Jobs run via FastAPI's built-in BackgroundTasks (in-process, no worker needed).

Downside: jobs are lost if the server restarts, and it doesn't scale across
multiple server processes. Fine for a handful of VAs on one machine. If you
outgrow this later, swap this file for a real DB -- nothing else changes.
"""

import time
import uuid
import traceback

JOBS: dict[str, dict] = {}


def create_job(task_name: str, params: dict, actor: str | None = None) -> str:
    """actor is the logged-in role ("admin" / "va") -- recorded so the Action
    log can show who ran what."""
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "task": task_name,
        "params": params,
        "actor": actor,
        "status": "running",
        "result": None,
        "error": None,
        "created_at": time.time(),
    }
    return job_id


def log_event(task_name: str, actor: str | None) -> str:
    """For instantaneous audit events (login/logout) rather than background
    work -- recorded straight into the Action log already "done", no
    background task involved."""
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "task": task_name,
        "params": {},
        "actor": actor,
        "status": "done",
        "result": None,
        "error": None,
        "created_at": time.time(),
    }
    return job_id


def run_job(job_id: str, fn, kwargs: dict):
    """Call this via BackgroundTasks.add_task(run_job, job_id, fn, kwargs)"""
    try:
        result = fn(**kwargs)
        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["result"] = result
    except Exception as exc:
        JOBS[job_id]["status"] = "failed"
        # The status pages show this directly to whoever is running the job --
        # a raw traceback isn't useful to them, just the exception's own
        # message (e.g. "Wrong API Key"). Full detail still goes to the
        # server's stdout/logs for whoever's actually debugging it.
        JOBS[job_id]["error"] = str(exc) or exc.__class__.__name__
        traceback.print_exc()


def get_job(job_id: str) -> dict | None:
    return JOBS.get(job_id)


def list_jobs() -> list[dict]:
    return sorted(JOBS.values(), key=lambda j: j["created_at"], reverse=True)
