from signin import make_user

from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import create_job, get_job
from ofmhelpers.features.account.keys import key_tail
from ofmhelpers.features.jobs.runner import DEACTIVATED, run_job
from ofmhelpers.redact import MASK

KEY = "sk-" + "a1B2c3D4" * 5


def _boom(**_kwargs):
    message = f"401 for key {KEY}"
    raise RuntimeError(message)


def _ran(**_kwargs):
    _ran.calls += 1
    return []


_ran.calls = 0


def test_failed_job_error_is_redacted():
    job_id = create_job("ai", {})
    run_job(job_id, _boom, {})
    job = get_job(job_id)
    assert job.status == "failed"
    assert KEY not in job.error
    assert MASK in job.error


def test_job_of_a_deactivated_user_is_not_run():
    user = make_user("deactivated-runner@example.com")
    job_id = create_job("ai", {}, actor=str(user.id))
    users.set_active(user.id, active=False)
    _ran.calls = 0
    run_job(job_id, _ran, {})
    job = get_job(job_id)
    assert _ran.calls == 0
    assert job.status == "failed"
    assert job.error == DEACTIVATED


def test_job_of_an_active_user_runs():
    user = make_user("active-runner@example.com")
    job_id = create_job("ai", {}, actor=str(user.id))
    _ran.calls = 0
    run_job(job_id, _ran, {})
    assert _ran.calls == 1
    assert get_job(job_id).status == "done"


def test_a_short_key_shows_no_tail():
    assert key_tail("abcdefg") is None
    assert key_tail("abcdefgh") == "efgh"
