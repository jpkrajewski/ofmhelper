"""/clone's SOP: a fixed handful of examples, set on the server and shown to
everyone who may open /clone (owner 2026-11-13/14). An example keeps its own
copies of what it shows: a person's old runs are deleted past the job cap
and source videos are swept, and an example must outlive both."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from signin import ADMIN_EMAIL, make_user, sign_in

from ofmhelpers.db.repositories import JobRepository
from ofmhelpers.db.stores import clone_examples as store
from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.features.assets import store as asset_store
from ofmhelpers.features.clone import examples
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.schemas import Role
from ofmhelpers.web.main import app

PROMPT = "Replace the guy in @Video1 with subject from @Image1, no tattoos"
SUBJECT = "/app/uploads/assets/abc__the-models-face.png"
VA = "va@example.com"


@pytest.fixture(autouse=True)
def stores(tmp_path, monkeypatch):
    """The asset store and the examples' own folder, both under tmp."""
    monkeypatch.setattr(asset_store, "assets_root", lambda: tmp_path / "assets")
    monkeypatch.setattr(examples, "uploads_root", lambda: tmp_path)


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    """One short real video: the frames are made with ffmpeg."""
    path = tmp_path_factory.mktemp("clip") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc=size=180x320:rate=10:duration=0.5", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip
    return path


def _shape(video: Path) -> str:
    probe = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries"]
    probe += ["stream=codec_name,width,height", "-of", "csv=p=0", str(video)]
    done = subprocess.run(probe, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _clone(tmp_path: Path, clip: Path, owner: int, status: str = "done") -> str:
    """A clone run of `owner`: its source in their part of the asset store
    and, when done, its result on disk."""
    source = asset_store.assets_root_for_user(owner) / f"{'a' * 64}__source.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(clip.read_bytes())
    params = {
        "prompt": PROMPT,
        "reference_images": [SUBJECT],
        "reference_videos": [str(source)],
    }
    job_id = create_job("clone", params, actor=str(owner))
    if status == "done":
        out = tmp_path / f"{job_id}.mp4"
        out.write_bytes(clip.read_bytes())
        JobRepository().update_status(
            job_id, "done", result=[{"name": out.name, "path": str(out)}]
        )
    return job_id


def _client(email: str, role: Role = Role.USER) -> TestClient:
    client = TestClient(app)
    sign_in(client, email, role)
    return client


@pytest.fixture
def owner() -> int:
    return make_user(ADMIN_EMAIL, Role.ADMIN).id


def test_an_example_shows_its_frame_result_and_prompt_to_everyone(
    tmp_path, clip, owner
):
    example = examples.add(_clone(tmp_path, clip, owner), "Remove tattoos")
    va = _client(VA, Role.VA)

    page = va.get("/clone/examples").text
    video = va.get(f"/clone/examples/{example.id}/video")
    poster = va.get(f"/clone/examples/{example.id}/poster")

    assert "sop-open" in va.get("/clone").text
    assert "Remove tattoos" in page
    assert "no tattoos" in page
    assert "Use this prompt" in page
    # Never the model's own picture.
    assert "the-models-face" not in page
    assert (video.status_code, video.headers["content-type"]) == (200, "video/mp4")
    assert (poster.status_code, poster.headers["content-type"]) == (200, "image/webp")
    # One video every browser plays: the original and the clone next to each
    # other (two 180x320 clips brought to 720 high: 406 + 406 wide).
    assert _shape(tmp_path / "sop" / str(example.id) / "compare.mp4") == "h264,812,720"


def test_an_example_outlives_its_run_and_its_source_video(tmp_path, clip, owner):
    """A person's old runs are deleted past the job cap and source videos
    are swept: the example keeps its own copies."""
    job_id = _clone(tmp_path, clip, owner)
    example = examples.add(job_id, "Simple replacement")

    JobRepository().delete(job_id)
    for left in (tmp_path / "assets").rglob("*__source.mp4"):
        left.unlink()
    (tmp_path / f"{job_id}.mp4").unlink()
    va = _client(VA, Role.VA)

    assert [(e.title, e.prompt, e.job_id) for e in store.every()] == [
        ("Simple replacement", PROMPT, None)
    ]
    assert PROMPT in va.get("/clone/examples").text
    assert va.get(f"/clone/examples/{example.id}/video").status_code == 200
    assert va.get(f"/clone/examples/{example.id}/poster").status_code == 200


def test_the_same_run_added_again_is_one_example_in_the_order_added(
    tmp_path, clip, owner
):
    first = _clone(tmp_path, clip, owner)
    second = _clone(tmp_path, clip, owner)

    a = examples.add(first, "First")
    examples.add(second, "Second")
    again = examples.add(first, "First, renamed")

    assert again.id == a.id
    assert [e.title for e in examples.shown()] == ["First, renamed", "Second"]


def test_only_a_finished_clone_with_its_files_becomes_an_example(tmp_path, clip, owner):
    running = _clone(tmp_path, clip, owner, status="running")
    no_result = _clone(tmp_path, clip, owner)
    (tmp_path / f"{no_result}.mp4").unlink()

    for job_id in (running, no_result, "no-such-job"):
        with pytest.raises(NotFoundError):
            examples.add(job_id, "x")

    assert store.every() == []
    assert not (tmp_path / "sop").exists()


def test_nothing_on_the_page_changes_the_examples(tmp_path, clip, owner):
    """Owner 2026-11-14: not dynamic. No route adds, renames or removes."""
    example = examples.add(_clone(tmp_path, clip, owner), "Kept")
    admin = _client(ADMIN_EMAIL, Role.ADMIN)

    answers = [
        admin.post("/clone/examples", json={"job_id": "x"}),
        admin.put(f"/clone/examples/{example.id}", json={"title": "y"}),
        admin.delete(f"/clone/examples/{example.id}"),
    ]

    assert [r.status_code for r in answers] == [405, 404, 404]
    assert [e.title for e in store.every()] == ["Kept"]
    assert "Add to SOP" not in admin.get("/clone").text


def test_a_removed_example_is_gone_with_its_files(tmp_path, clip, owner):
    example = examples.add(_clone(tmp_path, clip, owner), "Gone")
    va = _client(VA, Role.VA)

    examples.remove(example.id)
    examples.remove(example.id)

    assert store.every() == []
    assert not (tmp_path / "sop" / str(example.id)).exists()
    assert va.get(f"/clone/examples/{example.id}/video").status_code == 404
    assert va.get("/clone/examples/1/passwords").status_code == 422
