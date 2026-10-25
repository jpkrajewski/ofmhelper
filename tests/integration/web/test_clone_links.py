"""/clone-links over HTTP: Check answers the cleaned links or refuses the
paste, nothing is read or queued without Google Drive, Generate queues one
run per link on the clone-links queue, a Generate sent twice pays once,
Stop, a card's Recreate, the failure labels on the cards, and nobody sees
or acts on another person's runs. The run itself is in
test_clone_links_run.py, the reviews in test_clone_links_review.py, the
previews in test_clone_links_previews.py.

Runs here are held in the queue (`queued`): they stay `running`, nothing is
downloaded or paid; finished runs are made with the job store."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import json
import pickle
import re
import subprocess
import sys

import pytest
from clone_links_helpers import (
    OTHER,
    REEL,
    client,
    clone_links_env,  # noqa: F401 -- the fixture, used by `pytestmark`
    post_run,
    run,
)
from redis.exceptions import RedisError

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.kaiai.fake import FAKE_KEY as KIE_KEY
from ofmhelpers.cache import CLONE_LINKS_QUEUE
from ofmhelpers.config import settings
from ofmhelpers.web.clone_links.links import NOT_A_VIDEO_SITE
from ofmhelpers.web.clone_links.models import LinkPreview
from ofmhelpers.web.clone_links.review import NOT_CONNECTED
from ofmhelpers.web.clone_links.stop import stopped
from ofmhelpers.web.routers.generation import clone_links
from ofmhelpers.web.routers.registry import RESUMABLE, TOOLS
from ofmhelpers.web.schemas import Job
from ofmhelpers.web.stores import clone_from_links, users
from ofmhelpers.web.stores.jobs import (
    create_job,
    fail_job,
    get_job,
    hide_job,
    list_jobs_page,
    run_job,
)

pytestmark = pytest.mark.usefixtures("clone_links_env")

ANN = "ann@example.com"
BOB = "bob@example.com"
NO_CREDITS = "createTask rejected: {'code': 402, 'msg': 'Credits insufficient'}"


def _reel(n: int) -> str:
    return f"https://www.instagram.com/reel/R{n}/"


def _actor(email: str = ANN) -> str:
    return str(users.by_email(email).id)


def _runs(email: str = ANN) -> list[Job]:
    jobs, _ = list_jobs_page({clone_links.TASK}, 0, 500, _actor(email))
    return jobs


def _card(job_id: str) -> str:
    return f'data-job-id="{job_id}"'


@pytest.fixture
def queued(monkeypatch) -> list[tuple]:
    """What `/run` hands the queue (queue, function, arguments), held
    there: the runs stay queued -- `running` -- nothing downloaded or
    paid."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        clone_links,
        "enqueue_to",
        lambda queue, fn, *args, **_kw: calls.append((queue, fn, args)),
    )
    return calls


def _failed(link: str, batch: str, error: str = NO_CREDITS) -> str:
    job_id = create_job(
        clone_links.TASK, {"link": link, "batch": batch}, actor=_actor()
    )
    fail_job(job_id, error)
    return job_id


def _done(link: str, batch: str) -> str:
    job_id = create_job(
        clone_links.TASK, {"link": link, "batch": batch}, actor=_actor()
    )
    run_job(job_id, list, {})
    return job_id


def _remember(job_id: str) -> None:
    """The run's paid request, kept for recovery to finish."""
    tasks.remember(
        tasks.ProviderTask(
            job_id=job_id, provider="kie", model="wan", task_id="t", api_key=KIE_KEY
        )
    )


def _recreate(c, job_id: str) -> list[str]:
    r = c.get(f"/clone-links/recreate/{job_id}")
    assert r.status_code == 200, r.text
    return r.json()["links"]


# --- Check ---------------------------------------------------------------------


def test_check_answers_the_cleaned_links_each_once_and_queues_nothing():
    ann = client()
    pasted = (
        f"{REEL}?igsh=abc123\n"
        "instagram.com/reels/ABC123\n"  # the same reel, another spelling
        f"\n{OTHER}"
    )

    r = ann.post("/clone-links/check", data={"links": pasted})

    assert r.status_code == 200, r.text
    assert r.json() == {"links": [REEL, OTHER]}
    assert _runs() == []


def test_a_line_of_two_links_refuses_the_paste():
    """The VA's paste (2026-10-25): a reel copied twice onto one line. Never
    split or deduped behind their back: the whole paste is refused, naming
    the line."""
    ann = client()
    glued = f"{OTHER}https://www.instagram.com/reel/XYZ789/?utm_source=ig_web"

    r = ann.post("/clone-links/check", data={"links": f"{REEL}\n{glued}"})

    assert r.status_code == 400
    assert r.json()["detail"] == {
        "message": "Please put one link per line",
        "lines": [{"number": 2, "text": glued, "reason": "2 links on one line"}],
    }
    assert _runs() == []


def test_junk_and_a_site_with_no_videos_refuse_the_paste():
    """The VA's paste (2026-10-25): two lines of junk and a runpod docs page
    -- every one named with why."""
    ann = client()
    docs = "https://abc-8077.proxy.runpod.net/docs#/default/twotensors_post"

    r = ann.post(
        "/clone-links/check",
        data={"links": f"2. dsadasdasd\nasdasdasdasdas\n{docs}\n{REEL}"},
    )

    assert r.status_code == 400
    assert r.json()["detail"]["lines"] == [
        {"number": 1, "text": "2. dsadasdasd", "reason": "not a link"},
        {"number": 2, "text": "asdasdasdasdas", "reason": "not a link"},
        {"number": 3, "text": docs, "reason": NOT_A_VIDEO_SITE},
    ]


def test_too_many_links_are_refused(monkeypatch):
    monkeypatch.setenv("OFM_CLONE_LINKS_MAX", "1")

    r = client().post("/clone-links/check", data={"links": f"{REEL}\n{OTHER}"})

    assert r.status_code == 400
    assert r.json()["detail"] == "At most 1 links at a time (2 pasted)"


# --- Google Drive first ----------------------------------------------------------


def test_previews_without_google_drive_read_nothing(monkeypatch):
    read: list[list[str]] = []
    monkeypatch.setattr(
        clone_links, "preview_all", lambda urls, *_a: read.append(urls) or iter(())
    )

    r = client(google=False).post("/clone-links/previews", json={"urls": [REEL]})

    assert r.status_code == 400
    assert r.json()["detail"] == NOT_CONNECTED
    assert read == []


def test_generate_without_google_drive_is_refused_and_queues_nothing(queued):
    r = post_run(client(google=False), [REEL])

    assert r.status_code == 400
    assert r.json()["detail"] == NOT_CONNECTED
    assert queued == []
    assert _runs() == []


def test_previews_stream_a_line_per_link_unbuffered(monkeypatch):
    """`X-Accel-Buffering: no`: the host's nginx passes each line on as it
    comes, so the dialog's tiles fill one by one."""
    monkeypatch.setattr(
        clone_links,
        "preview_all",
        lambda urls, _user, price, _thumbs: (
            LinkPreview(url=u, seconds=2.0, max_s=price.max_s) for u in urls
        ),
    )

    r = client().post(
        "/clone-links/previews", json={"urls": [REEL, OTHER], "provider": "kie"}
    )

    assert r.status_code == 200, r.text
    assert r.headers["x-accel-buffering"] == "no"
    assert [json.loads(line)["url"] for line in r.text.splitlines()] == [REEL, OTHER]


# --- Generate ------------------------------------------------------------------


def test_generate_queues_one_run_per_link_on_the_clone_links_queue(queued):
    answer = run(client(), [REEL, OTHER])

    ids = answer["job_ids"]
    assert re.fullmatch(clone_links.BATCH, answer["batch"])
    assert [(q, fn, args[0], args[1]) for q, fn, args in queued] == [
        (CLONE_LINKS_QUEUE, run_job, ids[0], clone_links.run_clone_link),
        (CLONE_LINKS_QUEUE, run_job, ids[1], clone_links.run_clone_link),
    ]
    assert [args[2]["api_key"] for _q, _fn, args in queued] == [KIE_KEY, KIE_KEY]
    jobs = [get_job(j) for j in ids]
    assert [j.params["link"] for j in jobs] == [REEL, OTHER]
    assert [j.params["batch"] for j in jobs] == [answer["batch"]] * 2
    assert [j.status for j in jobs] == ["running", "running"]
    assert all("api_key" not in j.params for j in jobs)
    # Cards newest first, like the gallery.
    html = answer["html"]
    assert html.index(_card(ids[1])) < html.index(_card(ids[0]))


def test_the_same_generate_sent_twice_queues_its_runs_once(queued, monkeypatch):
    """The answer lost, the VA presses Generate again: the page resends its
    batch id and gets the runs already queued, paying nothing again --
    also once the batch's sent mark is gone (the runs in the DB answer)."""
    ann = client()
    batch = "0123456789abcdef"

    first = run(ann, [REEL, OTHER], batch=batch)
    again = run(ann, [REEL, OTHER], batch=batch)
    monkeypatch.setattr(clone_links, "_first_send", lambda _actor, _batch: True)
    later = run(ann, [REEL, OTHER], batch=batch)

    assert first["batch"] == again["batch"] == later["batch"] == batch
    assert again["job_ids"] == later["job_ids"] == first["job_ids"]
    assert len(queued) == 2
    assert len(_runs()) == 2
    for job_id in first["job_ids"]:
        assert _card(job_id) in again["html"]


def test_a_send_racing_the_first_of_its_generate_queues_nothing(queued, monkeypatch):
    monkeypatch.setattr(clone_links, "_first_send", lambda _actor, _batch: False)

    answer = run(client(), [REEL], batch="abcdef0123456789")

    assert answer["job_ids"] == []
    assert queued == []
    assert _runs() == []


def test_a_refused_send_leaves_its_generate_free_for_the_corrected_one(queued):
    ann = client()
    batch = "fedcba9876543210"

    refused = post_run(ann, [REEL], batch=batch, api_key="")
    answer = run(ann, [REEL], batch=batch)

    assert refused.status_code == 400
    assert len(answer["job_ids"]) == 1
    assert len(queued) == 1


@pytest.mark.parametrize("batch", ["abc", "../../etc/passwd", "AB" * 8, "0" * 33])
def test_a_batch_id_that_is_not_8_to_32_hex_is_refused(queued, batch):
    r = post_run(client(), [REEL], batch=batch)

    assert r.status_code == 422
    assert queued == []


def test_a_run_the_queue_refuses_is_failed_at_once(monkeypatch):
    """Never left `running` for recovery to call a restart: the card says
    to run it again, and the links after it are not made at all."""

    def refuse(*_args, **_kwargs):
        msg = "Connection refused"
        raise RedisError(msg)

    monkeypatch.setattr(clone_links, "enqueue_to", refuse)

    with pytest.raises(RedisError):
        post_run(client(), [REEL, OTHER])

    [job] = _runs()
    assert job.params["link"] == REEL
    assert job.status == "failed"
    assert job.error == clone_links.NOT_QUEUED


# --- Stop --------------------------------------------------------------------


def test_stop_stops_the_cards_whole_generate_and_fails_or_forgets_nothing(queued):
    ann = client()
    answer = run(ann, [REEL, OTHER])
    first, second = answer["job_ids"]
    _remember(first)

    r = ann.post(f"/clone-links/stop/{second}")

    assert r.status_code == 200, r.text
    assert r.json() == {"batch": answer["batch"], "running": 2}
    assert stopped(answer["batch"])
    assert [get_job(j).status for j in (first, second)] == ["running", "running"]
    assert tasks.recall(first) is not None


def test_stop_is_only_for_a_clone_links_card():
    ann = client()
    other_tool = create_job("clone", {"batch": "0123abcd"}, actor=_actor())

    assert ann.post(f"/clone-links/stop/{other_tool}").status_code == 404
    assert ann.post("/clone-links/stop/nothere").status_code == 404
    assert not stopped("0123abcd")


# --- Recreate ----------------------------------------------------------------


def test_a_failed_cards_recreate_brings_back_its_generates_links_left_to_redo():
    """Paste order; hidden ones left out (the clicked one kept); never one
    recovery is still finishing (paid, its request remembered), nor one
    queued again since in a newer Generate, done or still running."""
    ann = client()
    batch = "aaaa1111"
    first = _failed(_reel(1), batch)
    _done(_reel(2), batch)
    hidden = _failed(_reel(3), batch)
    hide_job(hidden, _actor(), hidden=True)
    finishing = _failed(_reel(4), batch, "kie.ai is still generating (task t)")
    _remember(finishing)
    _failed(_reel(5), batch)
    _failed(_reel(6), batch)
    last = _failed(_reel(7), batch)
    _done(_reel(5), "bbbb2222")  # redone since, and done
    create_job(  # redone since, still running
        clone_links.TASK, {"link": _reel(6), "batch": "cccc3333"}, actor=_actor()
    )

    assert _recreate(ann, last) == [_reel(1), _reel(7)]
    assert _recreate(ann, first) == [_reel(1), _reel(7)]
    assert _recreate(ann, hidden) == [_reel(1), _reel(3), _reel(7)]
    assert _recreate(ann, finishing) == []


def test_a_done_cards_recreate_brings_back_its_link_until_it_is_made_again():
    ann = client()
    done = _done(_reel(1), "dddd4444")

    assert _recreate(ann, done) == [_reel(1)]
    create_job(
        clone_links.TASK, {"link": _reel(1), "batch": "eeee5555"}, actor=_actor()
    )
    assert _recreate(ann, done) == []


# --- The page and its cards ----------------------------------------------------


def test_the_page_has_the_form_and_the_nav_entry():
    html = client().get("/clone-links").text

    assert 'href="/clone-links"' in html
    assert 'id="clone-links-form"' in html
    assert 'name="links"' in html
    assert 'id="clone-links-confirm"' in html
    assert "Google Drive not connected" not in html
    assert (
        "Google Drive not connected"
        in client(BOB, google=False).get("/clone-links").text
    )


def test_the_page_never_waits_on_google_for_the_drive_space(monkeypatch):
    """Drive space as cached only (`ask=False`), and the reel size the page
    estimates Drive space with."""
    asked: list[dict] = []
    seen: dict = {}
    real_render = clone_links.render

    def spy(request, name, **context):
        seen.update(context)
        return real_render(request, name, **context)

    monkeypatch.setattr(
        clone_links, "drive_space", lambda _user, **kw: asked.append(kw) or None
    )
    monkeypatch.setattr(clone_links, "render", spy)

    r = client().get("/clone-links")

    assert r.status_code == 200, r.text
    assert asked == [{"ask": False}]
    assert seen["drive_ready"] is True
    assert seen["reel_mb"] == settings.clone_links.reel_mb


def test_a_failed_card_says_why_in_a_few_words():
    ann = client()
    _failed(_reel(1), "ffff6666", "Could not download this reel")
    _failed(_reel(2), "ffff6666", "Stopped")
    _failed(_reel(3), "ffff6666", "kie.ai is still generating (task t-401)")

    cards = ann.get("/clone-links/gallery/latest").text

    assert re.findall(r'result-failed"[^>]*>([^<]+)<', cards) == [
        "Still generating",
        "Stopped",
        "Could not download",
    ]


def test_a_run_is_resumable_and_listed_like_every_tool():
    assert RESUMABLE[clone_links.TASK] is clone_links.run_clone_link
    assert TOOLS[clone_links.TASK].label == clone_links.LABEL


def test_a_queued_run_loads_in_a_fresh_worker_process(queued):
    """An RQ worker unpickles the job's function before importing anything
    of the web app: the queued one must load on its own (the run module
    alone was a circular import)."""
    run(client(), [REEL])
    [(_queue, _fn, (_job_id, fn, _kwargs))] = queued

    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pickle, sys; print(pickle.loads(sys.stdin.buffer.read()).__module__)",
        ],
        input=pickle.dumps(fn),
        capture_output=True,
        check=False,
    )

    assert loaded.returncode == 0, loaded.stderr.decode()
    assert loaded.stdout.decode().splitlines()[-1] == clone_links.__name__


# --- Nobody else's -------------------------------------------------------------


def test_nobody_sees_recreates_or_stops_another_persons_runs(queued):
    ann = client()
    bob = client(BOB)
    answer = run(ann, [REEL])
    [job_id] = answer["job_ids"]

    assert _card(job_id) in ann.get("/clone-links").text
    assert bob.get("/clone-links/reviews", params={"job": job_id}).json() == []
    assert bob.get(f"/clone-links/recreate/{job_id}").status_code == 404
    assert bob.post(f"/clone-links/stop/{job_id}").status_code == 404
    assert _card(job_id) not in bob.get("/clone-links").text
    assert _card(job_id) not in bob.get("/clone-links/gallery/latest").text
    assert not stopped(answer["batch"])
    # Ann's Generate id sent by Bob is a Generate of his own.
    [his] = run(bob, [REEL], batch=answer["batch"])["job_ids"]
    assert his != job_id
    assert get_job(his).actor == _actor(BOB)


def test_a_card_never_shows_another_persons_review(queued):
    """A review row under a reused job id that is not the card owner's is
    not theirs to see (`for_jobs(user_id=)`)."""
    ann = client()
    client(BOB)
    mine, reused = run(ann, [REEL, OTHER])["job_ids"]
    for job_id, owner, file_id in (
        (mine, _actor(), "ann-file"),
        (reused, _actor(BOB), "bob-file"),
    ):
        review_id = clone_from_links.start(job_id, 0, int(owner), None)
        assert review_id is not None
        assert clone_from_links.finish(
            review_id,
            claimed=None,
            drive_file_id=file_id,
            drive_url=f"https://drive.google.com/file/d/{file_id}/view",
        )

    states = {
        s["job_id"]: s["html"]
        for s in ann.get("/clone-links/reviews", params={"job": [mine, reused]}).json()
    }

    assert "ann-file" in states[mine]
    assert "bob-file" not in states[reused]
