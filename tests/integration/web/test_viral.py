"""/viral: an Instagram profile in, its most played reels out. Apify is
faked at the wire (apify/fake.py: the Instagram fake's profiles, videos on
its CDN); jobs run inline. sheep_love33's reels have 3M (pinned), 1.5M,
900k, 620k, 260k, 130k, 61k and 21k plays."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from signin import sign_in, sign_in_agency

from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.features.viral.media import add_audio, has_audio
from ofmhelpers.features.viral.models import ApifyReel
from ofmhelpers.features.viral.service import most_played
from ofmhelpers.integrations.apify.fake import (
    BROKE_KEY,
    START_USD,
    USD_PER_REEL,
    WRONG_KEY,
    fake_apify,
)
from ofmhelpers.media.ffmpeg import binary, run
from ofmhelpers.schemas.errors import error_reason
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.downloads import viral

PROFILE = "sheep_love33"
TOP_PLAYS = [3_000_000, 1_500_000, 900_000]


@pytest.fixture
def client(fake_externals, tmp_path, monkeypatch):
    monkeypatch.setattr(viral, "uploads_root", lambda: tmp_path / "uploads")
    monkeypatch.setenv("APIFY_API_KEYS", "fake-apify-key")
    monkeypatch.setenv("OFM_VIRAL_TOP", "3")
    c = TestClient(app)
    sign_in_agency(c)
    return c


def _search(client: TestClient, profile: str = PROFILE):
    r = client.post("/viral/run", data={"profile": profile})
    assert r.status_code == 202, r.text
    job = get_job(r.json()["job_id"])
    assert job is not None
    return job


def test_the_page_is_in_the_nav_and_starts_empty(client):
    html = client.get("/viral").text
    assert 'href="/viral"' in html
    assert 'id="viral-form"' in html
    assert "No searches yet" in html
    assert "200 newest reels a search · up to $0.52" in html


def test_a_search_keeps_the_most_played_reels_downloaded(client):
    job = _search(client)

    assert job.status == "done"
    assert [f["plays"] for f in job.result] == TOP_PLAYS
    assert all(
        Path(p).is_file() and Path(p).suffix == ".mp4" for p in job.local_paths()
    )
    # Read the newest 200 (the default), every paid add-on off; paid per reel.
    sent = fake_apify().inputs[0]
    assert sent["username"] == [PROFILE]
    assert sent["resultsLimit"] == 200
    assert not any(sent[k] for k in sent if k.startswith("include"))
    assert job.params["scanned"] == 8
    # Priced from the charged reels: Apify's own total lags behind.
    assert job.params["cost_usd"] == pytest.approx(8 * USD_PER_REEL + START_USD)


def test_a_search_reads_only_the_newest_reels_it_is_set_to(client, monkeypatch):
    monkeypatch.setenv("OFM_VIRAL_SCAN", "4")
    job = _search(client)

    assert fake_apify().inputs[0]["resultsLimit"] == 4
    assert job.params["scanned"] == 4
    assert len(job.result) == 3


def test_the_page_shows_the_search_with_counts_cost_and_download_all(client):
    job = _search(client)
    html = client.get("/viral").text

    assert f"@{PROFILE} · top 3 of 8 reels · $0.02" in html
    assert f'href="/viral/zip/{job.id}"' in html
    assert "3.0M plays" in html
    assert (
        html.index("3.0M plays") < html.index("1.5M plays") < html.index("900k plays")
    )
    assert html.count(f'href="/viral/files/{job.id}/') == 3


def test_download_all_is_one_zip_most_played_first(client):
    job = _search(client)
    r = client.get(f"/viral/zip/{job.id}")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f'filename="{PROFILE}-top-3.zip"' in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    codes = [f["code"] for f in job.result]
    assert names == [f"{i:02d}-{code}.mp4" for i, code in enumerate(codes, 1)]


def test_a_profile_link_or_handle_becomes_the_username(client):
    job = _search(client, "https://www.instagram.com/Sheep_Love33/?hl=en")
    assert job.params["profile"] == PROFILE


def test_text_that_is_no_profile_is_refused(client):
    r = client.post("/viral/run", data={"profile": "not a profile!!"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Paste an Instagram profile: @name or its link"


@pytest.mark.parametrize(
    ("profile", "reason"),
    [("no_such_person", "Profile not found"), ("closed_doors", "No reels")],
)
def test_a_profile_without_reels_fails_with_a_short_reason(client, profile, reason):
    job = _search(client, profile)

    assert job.status == "failed"
    assert error_reason(job.error) == reason
    assert f"@{profile}: {reason}" in client.get("/viral").text


def test_no_credit_left_fails_as_no_apify_credit(client, monkeypatch):
    monkeypatch.setenv("APIFY_API_KEYS", BROKE_KEY)
    job = _search(client)

    assert job.status == "failed"
    assert error_reason(job.error) == "No Apify credit"


def test_the_account_with_credit_left_runs_the_search(client, monkeypatch):
    monkeypatch.setenv("APIFY_API_KEYS", f"{BROKE_KEY},{WRONG_KEY},fake-apify-key")
    assert _search(client).status == "done"


def test_no_usable_key_fails_as_wrong_api_key(client, monkeypatch):
    monkeypatch.setenv("APIFY_API_KEYS", WRONG_KEY)
    job = _search(client)

    assert job.status == "failed"
    assert error_reason(job.error) == "Wrong API key"


def test_another_users_search_is_neither_shown_nor_zipped(client):
    job = _search(client)
    other = TestClient(app)
    sign_in(other)

    assert f"@{PROFILE}" not in other.get(f"/viral?job={job.id}").text
    assert other.get(f"/viral/zip/{job.id}").status_code == 404
    assert other.get(f"/viral/files/{job.id}/0").status_code == 404


def test_the_most_played_ignores_reels_without_video_and_repeats():
    def reel(code: str, plays: int, video: bool = True) -> ApifyReel:
        return ApifyReel(
            shortCode=code,
            url=f"https://www.instagram.com/p/{code}/",
            videoUrl="https://cdn/x.mp4" if video else None,
            videoPlayCount=plays,
        )

    reels = [reel("a", 5), reel("b", 9, video=False), reel("c", 7), reel("a", 5)]
    assert [r.code for r in most_played(reels, 30)] == ["c", "a"]


def test_plays_fall_back_to_views_and_hidden_counts_are_unknown():
    reel = ApifyReel(
        shortCode="x", url="u", videoViewCount=40, likesCount=-1, commentsCount=3
    )
    assert reel.plays == 40
    assert reel.likes is None


def test_a_picture_only_reel_gets_its_sound_back(tmp_path):
    video, audio = tmp_path / "v.mp4", tmp_path / "a.mp4"
    lavfi = [binary("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i"]
    run(
        [
            *lavfi,
            "testsrc=size=64x64:rate=10:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        60,
    )
    run([*lavfi, "sine=duration=1", "-c:a", "aac", str(audio)], 60)
    assert not has_audio(video)

    add_audio(video, audio)

    assert has_audio(video)
