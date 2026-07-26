"""
Backfill job rows for generation output files that exist in kieai_out/ but
have no matching "done" job row in Postgres -- e.g. a job whose completion
write never landed (crash between file-download and DB write, or a gap in
the one-time JSON->Postgres backfill). The files are the source of truth;
this only restores the job/gallery record pointing at them.

Idempotent: skips any file whose name already appears in some job's `result`
list, so re-running (e.g. every deploy) only picks up genuinely orphaned
files. Safe to run repeatedly and safe to run when there's nothing to do.

Recovered rows are marked done with whatever prompt/model kieai_out/
tasks.jsonl has logged for that file's task id; files with no tasks.jsonl
entry (predates that log, or from a provider that doesn't log there) get a
"prompt unknown" placeholder so the asset is at least visible/downloadable
again.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.web.db.models import JobRow
from ofmhelpers.web.db.session import session_scope

_MODEL_TO_TASK = {
    "bytedance/seedance-2": "seedance",
    "kling-3.0/video": "kling3",
    "nano-banana-pro": "nanobanana",
}
_ASSET_EXTS = (".png", ".jpg", ".jpeg", ".mp4")


def _load_task_log(task_log: Path) -> dict[str, dict]:
    tasks: dict[str, dict] = {}
    if not task_log.exists():
        return tasks
    for line in task_log.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        tasks[entry["taskId"]] = entry
    return tasks


def _referenced_filenames(session) -> set[str]:
    names: set[str] = set()
    for (result,) in session.query(JobRow.result).filter(JobRow.result.isnot(None)):
        if not isinstance(result, list):
            continue
        for item in result:
            if isinstance(item, dict) and item.get("name"):
                names.add(item["name"])
    return names


def recover_orphaned_jobs(
    out_dir: Path | None = None, task_log: Path | None = None
) -> int:
    """Insert a 'done' job per kieai_out/ file not referenced by any job's
    result. Returns the number of rows inserted."""
    out_dir = out_dir or Path(settings.kieai.out_dir)
    task_log = task_log or Path(settings.kieai.task_log)

    if not out_dir.exists():
        return 0

    tasks = _load_task_log(task_log)
    inserted = 0

    with session_scope() as s:
        known = _referenced_filenames(s)
        existing_ids = {row_id for (row_id,) in s.query(JobRow.id)}

        for path in sorted(out_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in _ASSET_EXTS:
                continue
            if path.name in known:
                continue

            logged = tasks.get(path.stem)
            if logged:
                task = _MODEL_TO_TASK.get(logged["model"])
                if task is None:
                    continue
                params = {"model": logged["model"], "prompt": logged["prompt"]}
                created_at = logged["createdAt"]
            else:
                task = "seedance" if path.suffix.lower() == ".mp4" else "nanobanana"
                params = {
                    "prompt": "(recovered - prompt unknown, not found in task log)"
                }
                created_at = path.stat().st_mtime

            job_id = str(uuid.uuid4())[:8]
            while job_id in existing_ids:
                job_id = str(uuid.uuid4())[:8]
            existing_ids.add(job_id)

            s.add(
                JobRow(
                    id=job_id,
                    task=task,
                    params=params,
                    actor="admin",
                    status="done",
                    result=[{"name": path.name, "path": str(out_dir / path.name)}],
                    error=None,
                    created_at=created_at,
                    preview=None,
                )
            )
            known.add(path.name)
            inserted += 1

    return inserted


def main() -> None:
    """CLI entry point: docker compose exec ofmhelpers python -m
    ofmhelpers.web.db.recover_orphaned_jobs"""
    count = recover_orphaned_jobs()
    print(f"Recovered {count} orphaned job row(s) from {settings.kieai.out_dir}.")


if __name__ == "__main__":
    main()
