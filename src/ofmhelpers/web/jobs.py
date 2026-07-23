"""
Job history: an in-memory dict (JOBS) for fast reads, kept in sync with a
JSON file on disk -- every create_job/log_event/run_job call persists too,
and load_jobs() (called once from main.py's lifespan at startup) reloads it
back into JOBS. Before this, JOBS was memory-only, so restarting the
container (or `docker build`ing a new image) silently wiped the /generate
gallery and Action log even though the generated files were still sitting
on disk untouched.

Jobs run via FastAPI's built-in BackgroundTasks (in-process, no worker needed).

No locking around the read-modify-write to disk -- two jobs finishing in the
exact same instant could race. Fine for a handful of VAs on one machine,
same tradeoff web/todos.py already makes. If you outgrow this later, swap
this file for a real DB -- nothing else changes.
"""

import json
import os
import time
import traceback
import uuid
from pathlib import Path

JOBS: dict[str, dict] = {}

STORE_FILE = Path(os.getenv("OFM_JOBS_FILE", "uploads/jobs.json"))

# Keep the persisted history from growing forever now that it survives
# restarts -- without a cap, a handful of VAs generating for months would
# leave thousands of dead rows in the file and on the Action log page.
MAX_JOBS = 500


def load_jobs() -> None:
    """Populate JOBS from disk. Call once at app startup (see main.py's
    lifespan) -- not at import time, so tests that never trigger the real
    app lifespan still start with a clean, empty JOBS like before."""
    if not STORE_FILE.exists():
        return
    try:
        JOBS.update(json.loads(STORE_FILE.read_text()))
    except json.JSONDecodeError:
        pass


def _save() -> None:
    if len(JOBS) > MAX_JOBS:
        stale = sorted(JOBS.values(), key=lambda j: j["created_at"], reverse=True)[
            MAX_JOBS:
        ]
        for j in stale:
            del JOBS[j["id"]]
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STORE_FILE.write_text(json.dumps(JOBS, indent=2))


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
    _save()
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
    _save()
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
    _save()


def get_job(job_id: str) -> dict | None:
    return JOBS.get(job_id)


def _result_matches_files(result: list[dict]) -> list[dict]:
    """Filters one job's result down to entries whose file(s) still exist on
    disk. Handles both shapes a job's "result" list comes in:
    - flat, one file per entry: {"name", "path"}  (seedance/kling3/
      nanobanana/fake_ai/clean_images/elevenlabs/radio_comms/scraper)
    - grouped by source URL: {"url", "success", "output_paths": [...]}
      (download_images/download_videos)
    """
    if "path" in result[0]:
        return [f for f in result if Path(f["path"]).is_file()]

    kept = []
    for entry in result:
        live = [p for p in entry.get("output_paths", []) if Path(p).is_file()]
        if live:
            kept.append({**entry, "output_paths": live})
        elif not entry.get("success"):
            kept.append(entry)  # a failed entry never had a file to lose
    return kept


def _prune_missing_files() -> bool:
    """Self-heals the job history after a result file is gone -- deleted
    through the file manager, or by hand on the server. Drops just the
    missing file(s) from a job's result, or the whole job if nothing in it
    still exists, so a removed file's gallery card disappears instead of
    turning into a dead link. Returns True if anything changed."""
    changed = False
    for job_id in list(JOBS):
        job = JOBS[job_id]
        result = job.get("result")
        if job.get("status") != "done" or not result:
            continue
        # Not every job's result is a list of file dicts -- e.g.
        # todo_drive_upload stores a plain Drive file ID string. Only the
        # generator/downloader jobs produce the shapes _result_matches_files
        # understands, so leave anything else untouched.
        if not isinstance(result, list) or not isinstance(result[0], dict):
            continue

        kept = _result_matches_files(result)
        if kept == result:
            continue

        changed = True
        if kept:
            job["result"] = kept
        else:
            del JOBS[job_id]
    return changed


def list_jobs() -> list[dict]:
    """Newest first."""
    if _prune_missing_files():
        _save()
    return sorted(JOBS.values(), key=lambda j: j["created_at"], reverse=True)
