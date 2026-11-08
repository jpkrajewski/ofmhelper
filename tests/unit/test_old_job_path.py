"""Jobs queued by the previous release name the old path of `run_job`
(ofmhelpers.web.stores.jobs); new jobs carry the real one. Goes with
src/ofmhelpers/web/stores/ in the next ship."""

from rq.utils import import_attribute

from ofmhelpers.db.stores.jobs import run_job


def test_a_job_queued_by_the_previous_release_still_finds_run_job():
    assert import_attribute("ofmhelpers.web.stores.jobs.run_job") is run_job


def test_new_jobs_carry_the_real_path():
    assert run_job.__module__ == "ofmhelpers.db.stores.jobs"
