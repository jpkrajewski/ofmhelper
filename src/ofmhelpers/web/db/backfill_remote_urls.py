"""
One-time backfill: jobs generated before seedance.py/kling.py/nbp.py started
keeping remote_url on a successful result only ever stored the local `path` --
their kie.ai hosted URL was discarded once the local download succeeded. This
re-derives it from kie.ai's recordInfo endpoint (KieAIClient.check_task) so
those old gallery entries can also serve from kie.ai instead of our local
proxy, exactly like a fresh generation does today.

Only works within kie.ai's retention window -- a task old enough that kie.ai
no longer has a record of it is skipped, not treated as an error.

Defaults to a dry run (prints what it would change, writes nothing). Pass
--apply to actually update Postgres.

Run in the container with:
    docker compose exec ofmhelpers python -m ofmhelpers.web.db.backfill_remote_urls
    docker compose exec ofmhelpers python -m ofmhelpers.web.db.backfill_remote_urls --apply

Deliberately uses print(), not the app logger (ofmhelpers/log.py): this is an
operator-facing CLI run by hand, and its stdout IS its output -- the report of
what would change is the whole point of the dry run. Logging it would subject
that report to a level/format meant for long-running services, and it'd be
suppressible via OFM_LOG_LEVEL. Every long-running code path logs instead.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from sqlalchemy import select

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.config import settings
from ofmhelpers.web.db.models import JobRow
from ofmhelpers.web.db.session import session_scope

# Tasks whose result shape is a flat [{"name", "path"}, ...] list produced by
# a KieAIClient generate_* call -- the only ones that ever had a remote_url to
# lose. Excludes fake_ai (never touches kie.ai) and the grouped download-*/
# clean-image/elevenlabs/radio-comms/scraper/replicate tasks (never had one).
KIEAI_TASKS = ("seedance", "kling3", "nanobanana")

# download_urls() names local files "{taskId}.{ext}" or "{taskId}_{i}.{ext}"
# -- the taskId is always the part before the first "." or "_{digit}".
_TASK_ID_RE = re.compile(r"^([0-9a-f]{32})(?:_\d+)?\.\w+$")


def extract_task_id(filename: str) -> str | None:
    m = _TASK_ID_RE.match(filename)
    return m.group(1) if m else None


def _candidate_jobs() -> list[dict]:
    """Done jobs of a kie.ai-backed task whose result has a local path but no
    remote_url yet."""
    candidates = []
    with session_scope() as s:
        rows = (
            s.execute(
                select(JobRow).where(
                    JobRow.task.in_(KIEAI_TASKS), JobRow.status == "done"
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            result = row.result or []
            if not result or not isinstance(result[0], dict):
                continue
            entry = result[0]
            if entry.get("path") and not entry.get("remote_url"):
                candidates.append({"id": row.id, "task": row.task, "result": result})
    return candidates


def _clients() -> list[KieAIClient]:
    """Try every configured key -- a task belongs to whichever key created it,
    and check_task has no other way to know which one that was."""
    keys = [settings.web.kie_ai_api_key_admin, settings.web.kie_ai_api_key_va]
    return [KieAIClient.from_env(api_key=k) for k in keys if k]


def run(apply: bool) -> None:
    candidates = _candidate_jobs()
    if not candidates:
        print("Nothing to backfill.")
        return

    clients = _clients()
    if not clients:
        print("No kie.ai API key configured -- nothing to check against.")
        return

    updated = 0
    for job in candidates:
        entry = job["result"][0]
        filename = Path(entry["path"]).name
        task_id = extract_task_id(filename)
        if task_id is None:
            print(f"[skip] {job['id']}: can't extract a taskId from {filename!r}")
            continue

        remote_url = None
        for client in clients:
            try:
                state, payload = client.check_task(task_id)
            except Exception as exc:
                print(f"[skip] {job['id']} ({task_id}): check failed: {exc}")
                continue
            if state == "success" and isinstance(payload, list) and payload:
                remote_url = payload[0]
                break

        if remote_url is None:
            print(f"[skip] {job['id']} ({task_id}): no longer available on kie.ai")
            continue

        print(
            f"[{'apply' if apply else 'dry-run'}] {job['id']} ({task_id}) -> {remote_url}"
        )
        if apply:
            entry["remote_url"] = remote_url
            with session_scope() as s:
                row = s.get(JobRow, job["id"])
                if row is not None:
                    row.result = job["result"]
            updated += 1

    if apply:
        print(f"Updated {updated} job(s).")
    else:
        print("Dry run only -- re-run with --apply to write these changes.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    args = parser.parse_args()
    run(apply=args.apply)


if __name__ == "__main__":
    main()
