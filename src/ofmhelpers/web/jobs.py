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


def create_job(task_name: str, params: dict) -> str:
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "task": task_name,
        "params": params,
        "status": "running",
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
    except Exception:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = traceback.format_exc()


def get_job(job_id: str) -> dict | None:
    return JOBS.get(job_id)


def list_jobs() -> list[dict]:
    return sorted(JOBS.values(), key=lambda j: j["created_at"], reverse=True)
