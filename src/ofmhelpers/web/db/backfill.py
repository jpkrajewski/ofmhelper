"""
One-time migration logic: copy the pre-Postgres JSON state files into the
database. Lives in the package (not scripts/) so it's importable and testable;
scripts/backfill_state.py is the thin CLI wrapper around it.

Reads uploads/jobs.json, uploads/todos.json and uploads/approval_tokens.json,
upserts every record into Postgres by primary key (so re-running never creates
duplicates), then archives the originals to uploads/archive/ instead of
deleting them -- a rollback safety net for one release cycle (spec step 8).

Idempotent: the upsert is a SQLAlchemy merge() keyed on the PK, so running it
twice against the same data is a no-op; archiving means a second real run
simply finds nothing left to do.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ofmhelpers.web.db.models import ApprovalTokenRow, JobRow, TodoRow
from ofmhelpers.web.db.session import session_scope

# Only these columns are copied -- render-time extras a router may have written
# into a job dict (status_url, created_at_display) are ignored.
_JOB_FIELDS = (
    "id",
    "task",
    "params",
    "actor",
    "status",
    "result",
    "error",
    "created_at",
    "preview",
)
_TODO_FIELDS = (
    "id",
    "model_name",
    "url",
    "comments",
    "checked",
    "created_at",
    "created_by",
    "asset_path",
    "asset_name",
    "approved",
    "rejected",
    "reject_comment",
    "drive_file_id",
    "drive_uploaded_at",
    "drive_upload_job_id",
)
_TOKEN_FIELDS = (
    "token",
    "todo_id",
    "asset_path",
    "created_at",
    "expires_at",
    "used_at",
)

# Older todos (written by import_todos) omit the asset/approval fields.
_TODO_DEFAULTS = {
    "comments": "",
    "checked": False,
    "created_by": None,
    "asset_path": None,
    "asset_name": None,
    "approved": False,
    "rejected": False,
    "reject_comment": None,
    "drive_file_id": None,
    "drive_uploaded_at": None,
    "drive_upload_job_id": None,
}


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def backfill_jobs(jobs: dict) -> int:
    """jobs.json is a dict of {job_id: job_dict}."""
    count = 0
    with session_scope() as s:
        for job in jobs.values():
            fields = {k: job.get(k) for k in _JOB_FIELDS}
            fields["params"] = fields.get("params") or {}
            s.merge(JobRow(**fields))
            count += 1
    return count


def backfill_todos(todos: list) -> int:
    count = 0
    with session_scope() as s:
        for todo in todos:
            fields = {k: todo.get(k, _TODO_DEFAULTS.get(k)) for k in _TODO_FIELDS}
            s.merge(TodoRow(**fields))
            count += 1
    return count


def backfill_tokens(tokens: list) -> int:
    count = 0
    with session_scope() as s:
        for tok in tokens:
            fields = {k: tok.get(k) for k in _TOKEN_FIELDS}
            s.merge(ApprovalTokenRow(**fields))
            count += 1
    return count


def _archive(path: Path, archive_dir: Path) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    path.rename(archive_dir / f"{path.name}.{int(time.time())}")


def backfill_dir(base: Path, archive: bool = True) -> dict[str, int]:
    """Backfill all three stores from `base` (e.g. uploads/). Returns a count
    per store. Archives each source file after a successful upsert unless
    archive=False."""
    archive_dir = base / "archive"
    counts = {"jobs": 0, "todos": 0, "approval_tokens": 0}

    for name, key, fn in (
        ("jobs.json", "jobs", backfill_jobs),
        ("todos.json", "todos", backfill_todos),
        ("approval_tokens.json", "approval_tokens", backfill_tokens),
    ):
        path = base / name
        data = _load_json(path)
        if data:
            counts[key] = fn(data)
            if archive:
                _archive(path, archive_dir)

    return counts


def main() -> None:
    """CLI entry point. Run in the container with:
    docker compose exec ofmhelpers python -m ofmhelpers.web.db.backfill"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir", default="uploads", help="directory holding the JSON state files"
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="don't move the JSON files to uploads/archive/ after import",
    )
    args = parser.parse_args()

    counts = backfill_dir(Path(args.dir), archive=not args.no_archive)
    print(
        f"Backfilled: {counts['jobs']} jobs, {counts['todos']} todos, "
        f"{counts['approval_tokens']} approval tokens."
    )
    if not args.no_archive and any(counts.values()):
        print(f"Archived source JSON files to {Path(args.dir) / 'archive'}.")


if __name__ == "__main__":
    main()
