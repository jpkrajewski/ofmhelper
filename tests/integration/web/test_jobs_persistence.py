# Copyright (C) 2026 jpkrajewski

from pathlib import Path

from signin import make_user

from ofmhelpers.db.jobs.repository import JobRepository
from ofmhelpers.db.jobs.schema import JobPage
from ofmhelpers.db.jobs.store import (
    create_job,
    fail_job,
    get_job,
    list_jobs,
    list_jobs_page,
    log_event,
    merge_params,
    redone_links,
    reopen_job,
)
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.integrations.ai_providers import tasks


def _someone(email: str = "someone@example.com") -> str:
    return str(make_user(email).id)


def test_create_job_persists_immediately():
    admin = _someone("boss@example.com")
    job_id = create_job("seedance", {"prompt": "a cat"}, actor=admin)

    job = get_job(job_id)
    assert job.task == "seedance"
    assert job.params == {"prompt": "a cat"}
    assert job.status == "running"
    assert job.actor == admin


def test_run_job_success_persists_result():
    job_id = create_job("seedance", {})
    run_job(job_id, lambda: [{"name": "out.mp4", "path": "/tmp/out.mp4"}], {})

    job = get_job(job_id)
    assert job.status == "done"
    assert job.result == [{"name": "out.mp4", "path": "/tmp/out.mp4"}]


def test_run_job_failure_persists_error():
    job_id = create_job("seedance", {})

    def boom():
        msg = "Wrong API Key"
        raise ValueError(msg)

    run_job(job_id, boom, {})

    job = get_job(job_id)
    assert job.status == "failed"
    assert job.error == "Wrong API Key"


def test_finish_time_is_stamped_on_done_and_failed_and_cleared_on_rerun():
    done = create_job("clone", {})
    assert get_job(done).finished_at is None
    run_job(done, list, {})
    job = get_job(done)
    assert job.finished_at is not None
    assert job.finished_at >= job.created_at

    failed = create_job("clone", {})
    run_job(failed, lambda: 1 / 0, {})
    assert get_job(failed).finished_at is not None

    reopen_job(failed)
    assert get_job(failed).finished_at is None


def test_jobs_survive_a_simulated_restart():
    job_id = create_job("kling3", {"prompt": "hi"}, actor=_someone())
    run_job(job_id, lambda: [{"name": "a.mp4", "path": "/tmp/a.mp4"}], {})

    restored = JobRepository().get(job_id)
    assert restored is not None
    assert restored.task == "kling3"
    assert restored.status == "done"
    assert restored.result == [{"name": "a.mp4", "path": "/tmp/a.mp4"}]


def test_list_jobs_drops_a_job_whose_flat_result_file_was_deleted(tmp_path):
    out_file = tmp_path / "gen.mp4"
    out_file.write_bytes(b"video")

    job_id = create_job("seedance", {})
    run_job(job_id, lambda: [{"name": "gen.mp4", "path": str(out_file)}], {})

    assert any(j.id == job_id for j in list_jobs())

    out_file.unlink()

    assert not any(j.id == job_id for j in list_jobs())
    assert get_job(job_id) is None


def test_list_jobs_keeps_a_job_still_running_even_with_no_result_yet():
    job_id = create_job("seedance", {})

    assert any(j.id == job_id for j in list_jobs())


def test_list_jobs_partially_prunes_a_grouped_result(tmp_path):
    kept_file = tmp_path / "kept.mp4"
    kept_file.write_bytes(b"video")
    gone_file = tmp_path / "gone.mp4"
    gone_file.write_bytes(b"video")
    gone_file.unlink()

    job_id = create_job("download_videos", {"urls": ["a", "b", "c"]})
    run_job(
        job_id,
        lambda: [
            {"url": "a", "success": True, "output_paths": [str(kept_file)]},
            {"url": "b", "success": True, "output_paths": [str(gone_file)]},
            {"url": "c", "success": False, "output_paths": [], "error": "boom"},
        ],
        {},
    )

    job = next(j for j in list_jobs() if j.id == job_id)
    urls = {r["url"] for r in job.result}
    assert urls == {"a", "c"}


def test_log_event_is_never_pruned_it_has_no_result():
    job_id = log_event("login", actor=_someone())

    assert any(j.id == job_id for j in list_jobs())


def test_list_jobs_ignores_a_non_list_result():
    job_id = create_job("todo_drive_upload", {"todo_id": "abc"})
    run_job(job_id, lambda: "1AbCdEfGhIjKlMnOp", {})

    job = next(j for j in list_jobs() if j.id == job_id)
    assert job.result == "1AbCdEfGhIjKlMnOp"


def test_list_jobs_page_filters_slices_and_reports_the_total():
    ids = [create_job("fake_ai", {"i": i}) for i in range(5)]
    create_job("download_videos", {})

    page = list_jobs_page({"fake_ai"}, 0, 2)
    assert page.total == 5
    assert [j.id for j in page.jobs] == [ids[4], ids[3]]

    page = list_jobs_page({"fake_ai"}, 4, 2)
    assert page.total == 5
    assert [j.id for j in page.jobs] == [ids[0]]

    assert list_jobs_page({"fake_ai"}, 99, 2) == JobPage(jobs=[], total=5)


def test_the_heal_keeps_a_tasks_own_result_fields(tmp_path):
    kept = tmp_path / "kept.mp4"
    kept.write_bytes(b"mp4")
    entry = {"name": kept.name, "path": str(kept), "url": "https://x/1", "seconds": 6.3}
    whole = create_job("fake_fetch", {})
    run_job(whole, lambda: [entry], {})
    pruned = create_job("fake_fetch", {})
    gone = {"name": "gone.mp4", "path": str(tmp_path / "gone.mp4"), "seconds": 1.0}
    run_job(pruned, lambda: [entry, gone], {})

    list_jobs()
    list_jobs_page({"fake_fetch"}, 0, 10)

    assert get_job(whole).result == [entry]
    assert get_job(pruned).result == [entry]


def test_list_jobs_page_only_touches_the_disk_for_the_page(tmp_path, monkeypatch):
    for i in range(4):
        out = tmp_path / f"a{i}.png"
        out.write_bytes(b"png")
        run_job(
            create_job("fake_ai", {}),
            lambda p=out: [{"name": p.name, "path": str(p)}],
            {},
        )

    checked = []
    real_is_file = Path.is_file

    def counting_is_file(self):
        checked.append(self)
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", counting_is_file)
    page = list_jobs_page({"fake_ai"}, 0, 2)

    assert page.total == 4
    assert len(page.jobs) == 2
    assert len(checked) == 2


def test_list_jobs_page_filters_and_pages_in_sql():
    someone, admin = _someone(), _someone("boss@example.com")
    va = [create_job("wan3", {"i": i}, actor=someone) for i in range(3)]
    create_job("wan3", {}, actor=admin)
    create_job("uniqify", {}, actor=someone)

    first = list_jobs_page({"wan3", "kling3"}, 0, 2, actor=someone)
    rest = list_jobs_page({"wan3"}, 2, 2, actor=someone).jobs
    assert first.total == 3
    pages = first.jobs + rest
    assert sorted(j.id for j in pages) == sorted(va)
    stamps = [j.created_at for j in pages]
    assert stamps == sorted(stamps, reverse=True)
    assert list_jobs_page({"wan3"}, 0, 10, actor=None).total == 4


def test_the_history_cap_drops_the_oldest_finished_jobs_never_a_running_one(
    monkeypatch,
):
    monkeypatch.setenv("OFM_JOBS_MAX_ENTRIES", "2")
    someone = _someone()
    queued = create_job("clone_links", {"link": "https://x/1"})
    finished = [log_event("login", actor=someone) for _ in range(4)]

    assert get_job(queued).status == "running"
    assert [job_id for job_id in finished if get_job(job_id)] == finished[-2:]


def test_a_job_whose_row_is_gone_is_never_run():
    job_id = create_job("clone_links", {"link": "https://x/1"})
    tasks.remember(
        tasks.ProviderTask(
            job_id=job_id,
            provider="kie",
            model="m",
            task_id="t1",
            api_key="k",
        ),
    )
    JobRepository().delete(job_id)
    called = []

    run_job(job_id, lambda: called.append("paid"), {})

    assert called == []
    assert get_job(job_id) is None
    assert tasks.recall(job_id) is None


def test_merge_params_adds_values_and_keeps_the_other_keys():
    job_id = create_job(
        "clone_links",
        {"link": "https://x/1", "batch": "ab12cd34", "seconds": 6},
    )
    assert get_job(job_id).params["link"] == "https://x/1"

    merge_params(job_id, {"source": "/a/v.mp4", "seconds": 7.5, "parts": [1, 2]})

    assert get_job(job_id).params == {
        "link": "https://x/1",
        "batch": "ab12cd34",
        "seconds": 7.5,
        "source": "/a/v.mp4",
        "parts": [1, 2],
    }


def test_merge_params_on_a_missing_job_does_nothing():
    merge_params("gone0000", {"source": "/a/v.mp4"})
    assert get_job("gone0000") is None


def test_redone_links_are_the_links_queued_again_later_running_or_done():
    me, other = _someone(), _someone("other@example.com")
    early = create_job("clone_links", {"link": "https://x/early"}, actor=me)
    stale = create_job("clone_links", {"link": "https://x/running"}, actor=me)
    fail_job(stale, "Timed out")
    after = get_job(stale).created_at
    create_job("clone_links", {"link": "https://x/running"}, actor=me)
    done = create_job("clone_links", {"link": "https://x/done"}, actor=me)
    run_job(done, list, {})
    failed = create_job("clone_links", {"link": "https://x/failed"}, actor=me)
    fail_job(failed, "Timed out")
    create_job("clone_links", {"link": "https://x/other-actor"}, actor=other)
    create_job("clone", {"link": "https://x/other-task"}, actor=me)
    create_job("clone_links", {"link": "https://x/not-asked"}, actor=me)
    asked = [
        "https://x/early",
        "https://x/running",
        "https://x/done",
        "https://x/failed",
        "https://x/other-actor",
        "https://x/other-task",
    ]

    assert get_job(early).status == "running"
    assert redone_links("clone_links", me, asked, after) == {
        "https://x/running",
        "https://x/done",
    }
    assert redone_links("clone_links", me, [], after) == set()
