"""A stored download is reused only inside a team (security audit,
`downloads-service:cross-user-reuse-of-login-fetched-file`): a link fetched
with A's cookies.txt is not handed to an unrelated B, who pastes it and gets
a fetch with B's own cookies; an owner and their VAs still share one fetch.

No network and no ffmpeg (`download_files.py`)."""

from pathlib import Path

import pytest
from download_files import FakeDownloads, fake_posters
from fastapi.testclient import TestClient
from signin import make_user, sign_in

from ofmhelpers.db.stores import cookies as login_cookies
from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.features.assets import store as asset_store
from ofmhelpers.features.downloads import media
from ofmhelpers.schemas import Role
from ofmhelpers.web.main import app

LINK = "https://gated.example/p/team-reuse"


class CookieRecordingDownloads(FakeDownloads):
    """`FakeDownloads` that also keeps the cookies each fetch was made with."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.cookies_used: list[str | None] = []

    def __call__(self, urls: list[str], cookies: str | None = None):
        self.cookies_used.append(cookies)
        return super().__call__(urls, cookies)


@pytest.fixture(autouse=True)
def assets(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    monkeypatch.setattr(asset_store, "assets_root", lambda: root)
    return root


@pytest.fixture
def downloads(tmp_path, monkeypatch) -> CookieRecordingDownloads:
    fake_posters(monkeypatch)
    fake = CookieRecordingDownloads(tmp_path / "downloads")
    monkeypatch.setattr(media, "download_all", fake)
    return fake


def _client(email: str, role: Role = Role.USER, owner_id: int | None = None):
    """A client signed in as `email`, with cookies of its own stored."""
    c = TestClient(app)
    user = sign_in(c, email, role)
    if owner_id is not None:
        users.set_owner(user.id, owner_id)
    login_cookies.write(user.id, f"# cookies of {email}\n", "cookies.txt")
    return c, user


def _paste(client: TestClient) -> str:
    r = client.post("/download-media/run", data={"urls": LINK})
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


def test_an_unrelated_user_fetches_the_link_with_their_own_cookies(downloads):
    a, _ = _client("reuse-a@example.com")
    b, _ = _client("reuse-b@example.com")

    a_job = _paste(a)
    b_job = _paste(b)

    assert downloads.fetched == [LINK, LINK]
    assert downloads.cookies_used == [
        "# cookies of reuse-a@example.com\n",
        "# cookies of reuse-b@example.com\n",
    ]
    a_path = get_job(a_job).local_paths()[0]
    b_path = get_job(b_job).local_paths()[0]
    assert a_path != b_path
    assert Path(b_path).read_bytes() == f"{LINK} #2".encode()


def test_an_owner_and_their_va_share_one_fetch(downloads):
    owner, owner_user = _client("reuse-owner@example.com")
    va, va_user = _client("reuse-va@example.com", Role.VA, owner_user.id)
    assert make_user("reuse-va@example.com", Role.VA).owner_id == owner_user.id

    owner_job = _paste(owner)
    va_job = _paste(va)

    assert downloads.fetched == [LINK]
    assert get_job(va_job).user_id == va_user.id
    assert get_job(va_job).local_paths() == get_job(owner_job).local_paths()
