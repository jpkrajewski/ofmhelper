"""/clone-links runs on the worker (web/clone_links/run.py): a paid re-run
never downloads its reel again nor loses its request, a stopped or halted
Generate pays for nothing more, a run whose row is gone does nothing, a
download leaves no folder behind and fails with a short fixed reason, a
result only hosted is saved here after all, and a failed export is said."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from contextlib import contextmanager
from pathlib import Path

import pytest
from clone_links_helpers import (
    OTHER,
    REEL,
    client,
    clone_links_env,  # noqa: F401 (the fixture)
    run,
)

from ofmhelpers import exports as exports_module
from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.base import Provider
from ofmhelpers.aigenproviders.errors import (
    ProviderNoCreditsError,
    ProviderUnavailableError,
)
from ofmhelpers.aigenproviders.kaiai.fake import FAKE_KEY as KIE_KEY
from ofmhelpers.aigenproviders.kaiai.fake import RESULT_HOST, fake_kie
from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.downloaders.models import DownloadResult
from ofmhelpers.utils import fake_media
from ofmhelpers.web.clone_links import run as run_module
from ofmhelpers.web.clone_links.stop import stop_batch, stopped
from ofmhelpers.web.routers.generation import clone
from ofmhelpers.web.routers.generation.clone_links import TASK
from ofmhelpers.web.routers.task_helpers import assets_root_for_actor
from ofmhelpers.web.schemas import Job, ResultFile
from ofmhelpers.web.schemas.errors import error_reason
from ofmhelpers.web.stores import clone_from_links
from ofmhelpers.web.stores.jobs import create_job, get_job, run_job

pytestmark = pytest.mark.usefixtures("clone_links_env")

LOGIN_WALL = (
    "video: ERROR: [Instagram] ABC123: The webpage request was redirected to the "
    "login page. You have exceeded the rate-limit for accessing posts anonymously"
)


@pytest.fixture
def ann():
    return client()


@pytest.fixture
def downloads(monkeypatch):
    """Every download a run starts (url, keyword arguments), still made by
    the faked downloaders."""
    calls = []
    real = run_module.downloaded

    @contextmanager
    def counted(url, cookiefile=None, **kwargs):
        calls.append((url, kwargs))
        with real(url, cookiefile, **kwargs) as result:
            yield result

    monkeypatch.setattr(run_module, "downloaded", counted)
    return calls


def _failing_downloads(monkeypatch, error="video: ERROR: Video unavailable"):
    @contextmanager
    def failing(url, _cookiefile=None, **_kwargs):
        yield DownloadResult(url=url, success=False, error=error)

    monkeypatch.setattr(run_module, "downloaded", failing)


def _no_downloads(monkeypatch):
    @contextmanager
    def never(*_a, **_k):
        msg = "a paid re-run downloaded its reel again"
        raise AssertionError(msg)
        yield

    monkeypatch.setattr(run_module, "downloaded", never)


def _done_run(c, link: str = REEL, **form) -> Job:
    [job_id] = run(c, [link], **form)["job_ids"]
    job = get_job(job_id)
    assert job.status == "done", job.error
    return job


def _rerun(job_id: str) -> Job:
    """The job run again the way recovery re-runs it (its params, the key
    its request was made with, the owner's asset store)."""
    job = get_job(job_id)
    run_job(
        job.id,
        run_module.run_clone_link,
        {
            **job.params,
            "job_id": job.id,
            "api_key": KIE_KEY,
            "assets_root": str(assets_root_for_actor(job.actor)),
        },
    )
    return get_job(job.id)


def _job_like(job: Job, **changes) -> str:
    """Another run of `job`'s Generate (same batch, image, settings), not
    run yet."""
    params = {
        k: v
        for k, v in job.params.items()
        if k not in {"source_video", "source_seconds"}
    }
    return create_job(TASK, {**params, **changes}, actor=job.actor)


def _remember_paid(job: Job) -> None:
    """Its kie.ai request remembered again, as after a deploy cut it off or
    its poll timed out (the fake's task is done)."""
    tasks.remember(
        tasks.ProviderTask(
            job_id=job.id,
            provider="kie",
            model=clone.CLONE_TARGETS["kie"].model,
            task_id=fake_kie().created[-1],
            api_key=KIE_KEY,
        )
    )


# --- A paid re-run (#1) ------------------------------------------------------


def test_a_run_saves_its_reel_on_the_job(ann):
    job = _done_run(ann)

    assert Path(job.params["source_video"]).is_file()
    assert job.params["source_seconds"] == pytest.approx(fake_media.MP4_SECONDS, 0.1)


def test_a_paid_rerun_reuses_its_saved_reel_and_pays_nothing_again(ann, monkeypatch):
    job = _done_run(ann)
    _remember_paid(job)
    _no_downloads(monkeypatch)

    again = _rerun(job.id)

    assert again.status == "done", again.error
    assert len(fake_kie().created) == 1, "resumed, never submitted again"
    assert tasks.recall(job.id) is None, "saved, so forgotten"
    assert len(clone_from_links.for_jobs([job.id])) == 1, "reviewed once"


def test_a_paid_rerun_that_cannot_download_its_reel_again_keeps_its_request(
    ann, monkeypatch
):
    job = _done_run(ann)
    Path(job.params["source_video"]).unlink()
    _remember_paid(job)
    _failing_downloads(monkeypatch)

    again = _rerun(job.id)

    assert again.status == "failed"
    assert again.error == run_module.REFETCH
    assert tasks.recall(job.id) is not None, "kept for recovery's next sweep"
    assert len(fake_kie().created) == 1


# --- Stop (#23) ---------------------------------------------------------------


def test_a_stopped_generate_pays_for_nothing_more(ann, downloads):
    job = _done_run(ann)
    batch = job.params["batch"]
    stop_batch(batch)
    waiting = _job_like(job, link=OTHER)

    stopped_run = _rerun(waiting)

    assert stopped(batch)
    assert get_redis().ttl(f"clone-links:stop:{batch}") > (
        settings.aigen.resume_max_age_s - 60
    )
    assert stopped_run.status == "failed"
    assert stopped_run.error == "Stopped"
    assert [url for url, _ in downloads] == [REEL], "the stopped run downloaded nothing"
    assert len(fake_kie().created) == 1, "and paid nothing"
    assert tasks.recall(waiting) is None


def test_stop_never_refuses_a_run_already_paid(ann, monkeypatch):
    job = _done_run(ann)
    _remember_paid(job)
    stop_batch(job.params["batch"])
    _no_downloads(monkeypatch)

    again = _rerun(job.id)

    assert again.status == "done", again.error
    assert len(fake_kie().created) == 1


def test_a_run_of_no_generate_is_never_stopped():
    assert stopped(None) is False
    assert stopped("") is False


# --- Halt after an account refusal (#22) --------------------------------------


def test_a_wrong_key_fails_the_rest_of_its_generate_without_downloading(ann, downloads):
    queued = run(ann, [REEL, OTHER], api_key="not-the-key")

    jobs = [get_job(j) for j in queued["job_ids"]]
    assert [j.status for j in jobs] == ["failed", "failed"]
    assert {j.error for j in jobs} == {"kie.ai: wrong API key"}
    assert len(downloads) == 1, "the second run failed before downloading"
    assert fake_kie().created == []


def test_no_credits_fails_the_rest_of_its_generate_without_downloading(
    ann, downloads, monkeypatch
):
    message = "kie.ai: credits insufficient (code 402)"

    def empty(*_a, **_k):
        raise ProviderNoCreditsError(message)

    monkeypatch.setattr(run_module, "_run_clone", empty)

    queued = run(ann, [REEL, OTHER])

    jobs = [get_job(j) for j in queued["job_ids"]]
    assert [j.status for j in jobs] == ["failed", "failed"]
    assert {j.error for j in jobs} == {message}
    assert len(downloads) == 1
    batch = jobs[0].params["batch"]
    assert (
        0
        < get_redis().ttl(f"clone-links:halt:{batch}")
        <= (settings.clone_links.halt_ttl_s)
    )


# --- A run whose row is gone (#3) ---------------------------------------------


def test_a_run_whose_job_is_gone_downloads_and_pays_nothing(downloads, tmp_path):
    with pytest.raises(run_module.RunGoneError):
        run_module.run_clone_link(
            "gone",
            KIE_KEY,
            str(tmp_path),
            link=REEL,
            provider="kie",
            prompt="p",
            resolution="720P",
            reference_images=[],
        )

    assert downloads == []
    assert fake_kie().created == []


# --- The download (#12, #45, #55, #25) -------------------------------------------


def test_a_download_is_one_post_and_leaves_no_folder_behind(ann, downloads, tmp_path):
    job = _done_run(ann)

    assert downloads == [(REEL, {"single": True})]
    assert list((tmp_path / "downloads").iterdir()) == []
    assert Path(job.params["source_video"]).is_file(), "the kept copy stays"


def test_a_reel_over_the_limit_is_refused_rounded_up_and_leaves_nothing(
    ann, monkeypatch, tmp_path
):
    monkeypatch.setattr(run_module, "duration_s", lambda _path, _timeout: 15.04)

    [job_id] = run(ann, [REEL])["job_ids"]

    job = get_job(job_id)
    assert job.status == "failed"
    assert job.error == "Too long for Wan 3.0: 15.1 s (the most is 15 s)"
    assert list((tmp_path / "downloads").iterdir()) == []
    assert fake_kie().created == []


@pytest.mark.parametrize(
    ("error", "cookies", "reason"),
    [
        ("video: ERROR: Video unavailable | images: 404", None, "Could not download"),
        (LOGIN_WALL, None, "Needs login"),
        (LOGIN_WALL, "# Netscape HTTP Cookie File\n", "Could not download"),
    ],
)
def test_a_failed_download_gives_a_short_fixed_reason(
    ann, monkeypatch, error, cookies, reason
):
    job = _done_run(ann)
    monkeypatch.setattr(run_module.login_cookies, "for_user", lambda _id: cookies)
    _failing_downloads(monkeypatch, error)

    failed = _rerun(_job_like(job, link=OTHER))

    assert failed.status == "failed"
    expected = {
        "Could not download": run_module.COULD_NOT_DOWNLOAD,
        "Needs login": run_module.NEEDS_LOGIN,
    }[reason]
    assert failed.error == expected
    page = ann.get("/clone-links").text
    assert "Video unavailable" not in page, "no downloader text shown"
    assert "redirected to the" not in page


def test_a_photo_post_has_no_video(ann, tmp_path):
    job = _done_run(ann)

    failed = _rerun(_job_like(job, link="https://www.instagram.com/p/PHOTO1/"))

    assert failed.status == "failed"
    assert failed.error == "No video at this link"
    assert list((tmp_path / "downloads").iterdir()) == []


# --- A result only hosted (#11) -------------------------------------------------


def _hosted_only(monkeypatch):
    """run_ai's answer when the paid result's local download failed: the
    hosted copy only (the request still remembered, as the runner leaves
    it)."""

    def hosted(job_id, api_key, _assets_root, provider, model, **_values):
        tasks.remember(
            tasks.ProviderTask(
                job_id=job_id,
                provider=provider,
                model=model,
                task_id="fake-task-hosted",
                api_key=api_key,
            )
        )
        url = f"https://{RESULT_HOST}/results/hosted-{job_id}.mp4"
        return [ResultFile(name=f"hosted-{job_id}.mp4", path=None, remote_url=url)]

    monkeypatch.setattr(clone, "run_ai", hosted)


def test_a_result_only_hosted_is_saved_here_and_reviewed(ann, monkeypatch):
    _hosted_only(monkeypatch)

    [job_id] = run(ann, [REEL])["job_ids"]

    job = get_job(job_id)
    assert job.status == "done", job.error
    [result] = job.result
    assert Path(result["path"]).is_file()
    [review] = clone_from_links.for_jobs([job_id])
    assert review.status == "done", review.error
    assert review.folder_name == "GOOD REELS"


def test_a_result_that_cannot_be_saved_keeps_its_paid_request(ann, monkeypatch):
    monkeypatch.setenv("OFM_AIGEN_DOWNLOAD_RETRY_WAITS_S", "[0, 0]")
    _hosted_only(monkeypatch)
    tries = []

    def broken(_self, url, _dest):
        tries.append(url)
        msg = f"kie.ai: could not download {url}"
        raise ProviderUnavailableError(msg)

    monkeypatch.setattr(Provider, "download", broken)

    [job_id] = run(ann, [REEL])["job_ids"]

    job = get_job(job_id)
    assert job.status == "failed"
    assert job.error == run_module.NOT_SAVED
    assert len(tries) == 3
    assert tasks.recall(job_id) is not None, "recovery fetches it on its next sweep"
    assert clone_from_links.for_jobs([job_id]) == []


# --- A failed export (#65) ------------------------------------------------------


def _broken_capcut(monkeypatch):
    def broken(*_a, **_k):
        msg = "ffmpeg failed: boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(exports_module, "apply_preset", broken)


def test_a_failed_export_fails_the_run_and_never_files_the_raw_video(ann, monkeypatch):
    """User rule 2026-10-25: no raw Wan file in Drive. The export is tried
    again, then the run fails keeping its paid request for recovery."""
    _broken_capcut(monkeypatch)

    [job_id] = run(ann, [REEL], export="capcut")["job_ids"]

    job = get_job(job_id)
    assert job.status == "failed"
    assert error_reason(job.error) == "Export failed"
    assert job.params[clone.EXPORT_FAILURES] == 1
    assert tasks.recall(job_id) is not None, "recovery exports it again, unpaid"
    assert clone_from_links.for_jobs([job_id]) == [], "nothing reviewed or filed"


def test_after_its_last_rerun_a_failed_export_lets_the_request_go(ann, monkeypatch):
    _broken_capcut(monkeypatch)
    [job_id] = run(ann, [REEL], export="capcut")["job_ids"]

    for _ in range(2):
        job = _rerun(job_id)

    assert job.status == "failed"
    assert job.params[clone.EXPORT_FAILURES] == 3
    assert tasks.recall(job_id) is None
