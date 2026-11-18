"""A stored job run again on the fakes (owner 2026-11-15: "can we replay any
job with a fake provider, fake client?"): from its row alone, here, never on
a queue, and never against a real service."""

from pathlib import Path

import pytest
from download_files import fake_downloads, fake_posters
from fastapi.testclient import TestClient
from signin import make_user, sign_in

from ofmhelpers.db.stores.jobs import create_job, get_job
from ofmhelpers.features.assets import store as asset_store
from ofmhelpers.features.generate.legacy import legacy_tools
from ofmhelpers.features.jobs import registry, replay
from ofmhelpers.features.trials import folder as trials
from ofmhelpers.integrations.ai_providers.kie.fake import FAKE_KEY as KIE_KEY
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.registry import TOOLS

LINK = "https://x.example/video/a"


@pytest.fixture(autouse=True)
def assets(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    monkeypatch.setattr(asset_store, "assets_root", lambda: root)
    return root


@pytest.fixture
def faked(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "true")


def _job(task: str, **params) -> str:
    return create_job(task, params, actor=str(make_user().id))


@pytest.mark.usefixtures("faked")
def test_a_download_runs_again_from_its_row(tmp_path, monkeypatch):
    fake_posters(monkeypatch)
    downloads = fake_downloads(monkeypatch, tmp_path)
    client = TestClient(app)
    sign_in(client)
    job_id = client.post("/download-media/run", data={"urls": LINK}).json()["job_id"]

    done = replay.replay(job_id)

    assert (done.job_id, done.status, done.error) == (job_id, "done", None)
    job = get_job(job_id)
    assert job is not None
    assert job.status == "done"
    # The same result: its file is still here, so it is not fetched twice.
    assert downloads.fetched == [LINK]


def test_a_paid_job_runs_on_its_providers_fake_key():
    job = get_job(_job("ai", provider="kie", model="m", prompt="x"))
    assert job is not None

    found = replay.replay_of(job)

    assert found.run is registry.RESUMABLE["ai"]
    assert found.kwargs["api_key"] == KIE_KEY
    assert found.kwargs["job_id"] == job.id
    assert (found.kwargs["provider"], found.kwargs["prompt"]) == ("kie", "x")


def test_a_trials_job_runs_from_its_args():
    job = get_job(_job("trials", label=trials.REMAKE, login=3, args={"keys": ["k1"]}))
    assert job is not None

    found = replay.replay_of(job)

    assert found.run is trials.RUNS[trials.REMAKE]
    assert found.kwargs["keys"] == ["k1"]
    assert found.kwargs["login_id"] == 3


@pytest.mark.parametrize(
    ("task", "params", "why"),
    [
        ("viral_reels", {}, "pays Apify"),
        ("ai", {"provider": "openai-real"}, "no fake for provider"),
        ("trials", {"label": trials.GENERATE, "login": 3}, "before rows kept"),
        ("nothing_known", {}, "has no replay"),
    ],
)
def test_what_cannot_run_again_says_why(task, params, why):
    job = get_job(_job(task, **params))
    assert job is not None

    with pytest.raises(replay.NotReplayableError, match=why):
        replay.replay_of(job)


def test_never_against_real_services(monkeypatch):
    """A worker never runs with the fakes: a replay is refused unless this
    process fakes every outside service (production refuses fakes itself)."""
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "false")
    job_id = _job("download_media", urls=[LINK])

    with pytest.raises(replay.NotReplayableError, match="only with OFM_FAKE_EXTERNALS"):
        replay.replay(job_id)

    job = get_job(job_id)
    assert job is not None
    assert job.status == "running"  # nothing ran


def test_every_tool_is_replayable_or_says_why_not():
    known = (
        set(registry.RESTARTS)
        | set(registry.RESUMABLE)
        | {trials.TASK}
        | set(replay.NOT_REPLAYABLE)
        | set(legacy_tools().entries)
    )
    assert sorted(set(TOOLS) - known) == []
