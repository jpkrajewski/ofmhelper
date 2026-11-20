from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from download_files import fake_downloads, fake_posters
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from httpx import Response
from idempotent import keyed
from signin import sign_in
from sqlalchemy import update

from ofmhelpers.db.idempotency_keys.schema import Asked, Outcome
from ofmhelpers.db.jobs.store import list_jobs_page
from ofmhelpers.db.models import IdempotencyKeyRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.features.assets import store as asset_store
from ofmhelpers.features.downloads import media as download_media
from ofmhelpers.features.downloads import service as download_service
from ofmhelpers.features.idempotency import service as idempotency
from ofmhelpers.features.refusals import UnavailableError
from ofmhelpers.web.idempotency import IdempotentRoute, marked
from ofmhelpers.web.main import app
from ofmhelpers.web.routers import ROUTERS

A = "https://x.example/video/a"
B = "https://x.example/video/b"
RUN = "/download-media/run"


@pytest.fixture(autouse=True)
def assets(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    monkeypatch.setattr(asset_store, "assets_root", lambda: root)
    return root


@pytest.fixture(autouse=True)
def downloads(tmp_path, monkeypatch):
    fake_posters(monkeypatch)
    return fake_downloads(monkeypatch, tmp_path)


@pytest.fixture
def client() -> TestClient:
    c = TestClient(app)
    c.user = sign_in(c)  # type: ignore[attr-defined]
    return c


def _run(client: TestClient, urls: str, key: dict[str, str]) -> Response:
    return client.post(RUN, data={"urls": urls}, headers=key)


def _jobs(client: TestClient) -> int:
    actor = str(client.user.id)  # type: ignore[attr-defined]
    return list_jobs_page({"download_media"}, 0, 50, actor).total


def _asked(client: TestClient, key: dict[str, str], urls: str) -> Asked:
    return Asked(
        user_id=client.user.id,  # type: ignore[attr-defined]
        key=key["Idempotency-Key"],
        path=f"POST {RUN}",
        fingerprint=idempotency.fingerprint([f"urls={urls}"]),
    )


def _backdate(asked: Asked, by: timedelta) -> None:
    row = IdempotencyKeyRow
    with session_scope() as s:
        s.execute(
            update(row)
            .where(row.user_id == asked.user_id, row.key == asked.key)
            .values(created_at=row.created_at - by)
        )


def test_ten_clicks_with_one_key_start_one_download(client, downloads):
    key = keyed()

    first, *rest = [_run(client, A, key) for _ in range(10)]

    assert first.status_code == 202
    assert {r.status_code for r in rest} == {200}
    assert all(r.json() == first.json() for r in rest)
    assert all(r.headers["Idempotent-Replayed"] == "true" for r in rest)
    assert downloads.fetched == [A]
    assert _jobs(client) == 1


def test_twenty_clicks_at_once_start_one_download(client, downloads):
    key = keyed()

    with ThreadPoolExecutor(20) as pool:
        answers = list(pool.map(lambda _: _run(client, A, key), range(20)))

    codes = [r.status_code for r in answers]
    assert codes.count(202) == 1
    assert set(codes) <= {200, 202, 409}
    assert downloads.fetched == [A]
    assert _jobs(client) == 1


def test_a_run_without_a_key_is_refused(client, downloads):
    refused = client.post(RUN, data={"urls": A})

    assert refused.status_code == 400
    assert refused.json() == {
        "detail": "Send an Idempotency-Key header, one per action"
    }
    assert downloads.fetched == []


def test_a_key_sent_again_with_other_links_is_refused(client, downloads):
    key = keyed()
    _run(client, A, key)

    mismatch = _run(client, B, key)

    assert mismatch.status_code == 400
    assert mismatch.json() == {
        "detail": "This Idempotency-Key was used for a different request"
    }
    assert downloads.fetched == [A]


def test_a_refused_request_frees_its_key(client, downloads):
    key = keyed()

    assert _run(client, " ", key).status_code == 400
    assert _run(client, A, key).status_code == 202
    assert downloads.fetched == [A]


def test_a_key_still_being_handled_asks_to_come_back(client, downloads):
    key = keyed()
    assert idempotency.claim(_asked(client, key, A)).outcome is Outcome.NEW

    busy = _run(client, A, key)

    assert busy.status_code == 409
    assert busy.headers["Retry-After"] == "1"
    assert downloads.fetched == []


def test_a_key_left_by_a_crashed_request_is_taken_over(client, downloads):
    key = keyed()
    asked = _asked(client, key, A)
    idempotency.claim(asked)
    _backdate(asked, timedelta(hours=1))

    assert _run(client, A, key).status_code == 202
    assert downloads.fetched == [A]


def test_a_crash_after_the_work_started_never_runs_it_again(downloads, monkeypatch):
    started = download_media.start

    def start_then_crash(*args, **kwargs):
        started(*args, **kwargs)
        msg = "the cards did not render"
        raise RuntimeError(msg)

    monkeypatch.setattr(download_media, "start", start_then_crash)
    crashing = TestClient(app, raise_server_exceptions=False)
    crashing.user = sign_in(crashing)  # type: ignore[attr-defined]
    key = keyed()

    first, again = _run(crashing, A, key), _run(crashing, A, key)

    assert (first.status_code, again.status_code) == (500, 500)
    assert again.headers["Idempotent-Replayed"] == "true"
    assert downloads.fetched == [A]
    assert _jobs(crashing) == 1


def test_an_outside_service_down_frees_the_key_to_try_again(
    client, downloads, monkeypatch
):
    started = download_media.start

    def down(*_args, **_kwargs):
        msg = "The download service did not answer"
        raise UnavailableError(msg)

    monkeypatch.setattr(download_media, "start", down)
    key = keyed()
    assert _run(client, A, key).status_code == 502

    monkeypatch.setattr(download_media, "start", started)
    assert _run(client, A, key).status_code == 202
    assert downloads.fetched == [A]


def test_a_late_answer_to_a_taken_over_key_is_not_kept(client):
    asked = _asked(client, keyed(), A)
    first = idempotency.claim(asked).held
    assert first is not None
    _backdate(asked, timedelta(hours=1))
    late = first.model_copy(
        update={"claimed_at": first.claimed_at - timedelta(hours=1)}
    )

    second = idempotency.claim(asked).held
    idempotency.finish(late, 202, {"job_id": "late"})

    assert second is not None
    assert idempotency.claim(asked).outcome is Outcome.BUSY
    idempotency.finish(second, 202, {"job_id": "taken over"})
    assert idempotency.claim(asked).response_body == {"job_id": "taken over"}


def test_keys_older_than_their_keep_time_are_removed(client):
    old, fresh = _asked(client, keyed(), A), _asked(client, keyed(), B)
    idempotency.claim(old)
    idempotency.claim(fresh)
    _backdate(old, timedelta(days=2))

    assert idempotency.remove_old() == 1
    assert idempotency.claim(old).outcome is Outcome.NEW
    assert idempotency.claim(fresh).outcome is Outcome.BUSY


def test_a_link_still_downloading_is_not_downloaded_twice(
    client, downloads, monkeypatch
):
    monkeypatch.setattr(download_service, "enqueue", lambda *_a, **_k: None)
    first = _run(client, A, keyed()).json()["job_id"]
    _run(client, B, keyed())

    again = _run(client, A, keyed())

    assert again.status_code == 202
    assert again.json() == {"job_id": None, "job_ids": [], "again": [first]}
    assert _jobs(client) == 2


def test_every_route_asking_for_a_key_handles_it():
    routes = [r for router in ROUTERS for r in router.routes]
    asking = [r for r in routes if isinstance(r, APIRoute) and marked(r)]

    assert {r.path for r in asking} >= {
        RUN,
        "/spoof/run",
        "/clone/run",
        "/clone-long/run",
        "/ai/run",
    }
    assert all(isinstance(r, IdempotentRoute) for r in asking)
