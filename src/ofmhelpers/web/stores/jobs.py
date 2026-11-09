"""The path RQ jobs carried until `ofmhelpers/_moved.py` went (2026-11-08): a
job queued by the old app while the workers restart for that deploy still names
it. DELETE this package in the next ship, with
tests/unit/test_old_job_path.py."""

from ofmhelpers.db.stores.jobs import run_job

__all__ = ["run_job"]
