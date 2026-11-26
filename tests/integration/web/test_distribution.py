# Copyright (C) 2026 jpkrajewski

import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from signin import sign_in_agency

from ofmhelpers.config import settings
from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.models import DeviceFields, LoginFields
from ofmhelpers.db.distribution import store
from ofmhelpers.db.distribution.schema import ModelPreset, PostStatus
from ofmhelpers.db.self_links import store as self_links
from ofmhelpers.db.short_links.schema import SelfClick
from ofmhelpers.db.tracked_accounts import store as tracked_accounts
from ofmhelpers.db.users import store as users
from ofmhelpers.features.distribution import publish, sweep
from ofmhelpers.features.distribution.report import report
from ofmhelpers.features.distribution.service import Distribution, Upload
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.features.storage import jobs as storage_jobs
from ofmhelpers.features.storage.client import storage_of
from ofmhelpers.integrations.outbound import FakeReply
from ofmhelpers.integrations.pinterest.fake import FakePinterest, fake_pinterest
from ofmhelpers.integrations.storage.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.web.main import app

AGENCY = "agency@example.com"
MY_DRIVE = f"mydrive-{AGENCY}"
ROOT = "agency-root-folder"
DOMAIN = "go.ascendglobal.cash"


def _model(key: str, title: str, handle: str) -> ModelPreset:
    return ModelPreset(
        key=key,
        creator=key,
        title=title,
        board=title,
        target="stacked",
        destination=f"https://www.stacked.com/@{key}/membership",
        domain=DOMAIN,
        slug=key,
        instagram=[handle],
        descriptions=[f"{title}, more at the link.", f"{title} today, at the link."],
    )


DANE = _model("dane", "Dane Example", "dane.example")
CLARK = _model("clark", "Clark Example", "clark.example")


def cookies_txt(session: str) -> bytes:
    rows = [
        (".pinterest.com", "TRUE", "_pinterest_sess", session),
        ("www.pinterest.com", "FALSE", "csrftoken", "csrf-www"),
        (".instagram.com", "TRUE", "sessionid", "not-pinterest"),
    ]
    lines = ["# Netscape HTTP Cookie File"] + [
        f"{d}\t{f}\t/\tTRUE\t1999999999\t{n}\t{v}" for d, f, n, v in rows
    ]
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
def pinterest(fake_externals, monkeypatch) -> FakePinterest:
    monkeypatch.setenv("OFM_PINTEREST_UPLOAD_POLL_S", "0.01")
    monkeypatch.setenv("OFM_DISTRIBUTION_HANDLE_SHARE", "0")
    return fake_pinterest()


def _instagram(fake: FakeDrive, phone: int, handle: str, creator: str) -> str:
    assert devices.add_login(phone, LoginFields(service="instagram", username=handle))
    tracked_accounts.track("instagram", handle, creator)
    storage_jobs.sync()
    (account,) = fake.named(f"{handle} - Anna", ROOT)
    return account


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path) -> FakeDrive:
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    fake.seed_folder("DEV - ALL ACCOUNTS", MY_DRIVE, item_id=ROOT, owner=AGENCY)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    users.set_drive_root(users.agency_id(), ROOT)
    phone = devices.add_device(
        DeviceFields(
            label="Phone 01",
            model_id="iphone-13",
            color_id="blue",
            owner="Anna",
        ),
    )
    ann = _instagram(fake, phone, "ann.ig", "dane")
    (posted,) = fake.named("POSTED", ann)
    fake.add_file(posted, "IMG_1.jpg", "image/jpeg")
    fake.add_file(posted, "IMG_2.jpg", "image/jpeg")
    threads = fake.seed_folder("THREADS", ann, owner=AGENCY)
    reel = fake.seed_folder("VID_1", threads, owner=AGENCY)
    fake.add_file(reel, "IMG_9001.JPG", "image/jpeg")
    fake.add_file(reel, "IMG_9002.JPG", "image/jpeg")
    bob = _instagram(fake, phone, "bob.ig", "clark")
    (bob_posted,) = fake.named("POSTED", bob)
    fake.add_file(bob_posted, "CLARK_1.jpg", "image/jpeg")
    storage_jobs.sync()
    self_links.add_domain(DOMAIN, None)
    store.repository().save_preset(users.agency_id(), DANE)
    store.repository().save_preset(users.agency_id(), CLARK)
    return fake


@pytest.fixture
def pictures(monkeypatch):

    def download(_user_id, _post, here):
        here.mkdir(parents=True, exist_ok=True)
        path = here / "media.jpg"
        Image.new("RGB", (600, 800), (30, 30, 30)).save(path, "JPEG")
        return path

    monkeypatch.setattr(publish, "_download", download)


def _dist() -> Distribution:
    uid = users.agency_id()
    storage = storage_of(uid, interactive=True)
    return Distribution(uid=uid, scope=uid, storage=storage)


def _add(*sessions: str) -> list:
    return _dist().add(
        [Upload(name=f"{s}.txt", file=io.BytesIO(cookies_txt(s))) for s in sessions],
    )


def _channels() -> list:
    return store.repository().channels_of(users.agency_id())


def _signed_in() -> TestClient:
    client = TestClient(app)
    sign_in_agency(client)
    return client


def test_bulk_upload_adds_each_account_with_every_models_content(pinterest, drive):
    page = _signed_in().post(
        "/distribution/accounts",
        files=[
            ("files", ("one.txt", cookies_txt("one"), "text/plain")),
            ("files", ("two.txt", cookies_txt("two"), "text/plain")),
        ],
    )

    assert page.status_code == 200, page.text
    assert sorted(c.account_name for c in _channels()) == ["pin_one", "pin_two"]
    assert [store.repository().counts(c.id).queued for c in _channels()] == [5, 5]
    assert "Added @pin_one" in page.text
    assert "5 files to pin" in page.text


def test_an_empty_upload_is_refused():
    page = _signed_in().post(
        "/distribution/accounts",
        files=[("files", ("empty.txt", b"", "text/plain"))],
    )

    assert page.status_code == 422
    assert _channels() == []


def test_the_global_upload_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(settings.web, "max_upload_mb", 1)

    page = _signed_in().post(
        "/distribution/accounts",
        files=[("files", ("big.txt", b"x" * (1024 * 1024 + 1), "text/plain"))],
    )

    assert page.status_code == 422
    assert _channels() == []


def test_a_file_without_a_pinterest_login_is_reported_and_the_rest_go_on(
    pinterest,
    drive,
):
    added = _dist().add(
        [
            Upload(name="bad.txt", file=io.BytesIO(b"# Netscape HTTP Cookie File\n")),
            Upload(name="good.txt", file=io.BytesIO(cookies_txt("good"))),
        ],
    )

    assert [(a.file, a.account, bool(a.error)) for a in added] == [
        ("bad.txt", "", True),
        ("good.txt", "pin_good", False),
    ]


def test_each_pin_carries_its_own_models_board_title_caption_and_link(
    pinterest,
    drive,
    pictures,
):
    _add("one")
    (channel,) = _channels()

    for _ in range(5):
        sweep.start_next(channel)

    posts = store.repository().recent([channel.id], 5)
    assert {p.preset for p in posts} == {"dane", "clark"}
    for post in posts:
        model = DANE if post.preset == "dane" else CLARK
        assert post.status is PostStatus.published
        assert post.title == model.title
        assert post.description in model.descriptions
        assert post.link == f"https://{DOMAIN}/{model.key}"
    boards = {b.name: b.id for b in pinterest.boards}
    assert {p.body["board_id"] for p in pinterest.pins} == {
        boards["Dane Example"],
        boards["Clark Example"],
    }


def test_one_link_per_model_whatever_the_number_of_accounts(pinterest, drive, pictures):
    _add("one", "two")
    for channel in _channels():
        for _ in range(5):
            sweep.start_next(channel)

    links = store.repository().model_links(users.agency_id())
    assert sorted(m.url for m in links) == [
        f"https://{DOMAIN}/clark",
        f"https://{DOMAIN}/dane",
    ]


def test_the_accounts_do_not_pin_the_same_file_first(pinterest, drive, pictures):
    _add("one", "two")
    one, two = _channels()

    sweep.start_next(one)
    (pinned,) = store.repository().recent([one.id], 1)
    following = store.repository().next_post(two, 3)

    assert following is not None
    assert following.file_id != pinned.file_id


def test_rate_limited_the_pin_goes_back_in_line_and_the_account_waits(
    pinterest,
    drive,
    pictures,
):
    _add("one")
    (channel,) = _channels()
    pinterest.fail_next = [FakeReply.text("slow down", status=429)]

    sweep.start_next(channel)

    (post,) = store.repository().recent([channel.id], 1)
    after = store.repository().channel(channel.id)
    assert after is not None
    assert (post.status, post.attempts) == (PostStatus.queued, 0)
    assert after.resume_at is not None
    assert after.resume_at > time.time()
    assert pinterest.pins == []


def test_logged_out_the_account_stops_until_new_cookies(pinterest, drive, pictures):
    _add("one")
    (channel,) = _channels()
    pinterest.logged_out = True

    sweep.start_next(channel)

    stopped = store.repository().channel(channel.id)
    assert stopped is not None
    assert stopped.error
    assert store.repository().running() == []
    pinterest.logged_out = False
    _add("one")
    assert [c.id for c in store.repository().running()] == [channel.id]


def test_the_sweep_pins_only_when_due_at_jittered_times(
    pinterest,
    drive,
    pictures,
    monkeypatch,
):
    monkeypatch.setenv("OFM_DISTRIBUTION_SCHEDULED", "true")
    _add("one")
    (channel,) = _channels()
    slot = sweep.slot_s()

    sweep.run_round()
    first = store.repository().channel(channel.id)
    assert first is not None
    assert first.next_at is not None
    assert time.time() <= first.next_at <= time.time() + slot
    assert pinterest.pins == []

    store.repository().set_next(channel.id, time.time() - 1)
    sweep.run_round()
    after = store.repository().channel(channel.id)
    assert after is not None
    assert after.next_at is not None
    assert len(pinterest.pins) == 1
    gap = after.next_at - time.time()
    assert slot * 0.69 <= gap <= slot * 1.31


def test_off_schedule_the_sweep_only_fills_queues(
    pinterest,
    drive,
    pictures,
    monkeypatch,
):
    monkeypatch.setenv("OFM_DISTRIBUTION_SCHEDULED", "false")
    _add("one")
    (channel,) = _channels()
    store.repository().set_next(channel.id, time.time() - 1)

    sweep.run_round()

    assert pinterest.pins == []


def test_an_ig_pin_names_the_handle_with_no_caption_and_no_link(
    pinterest,
    drive,
    pictures,
    monkeypatch,
):
    monkeypatch.setenv("OFM_DISTRIBUTION_HANDLE_SHARE", "1")
    _add("one")
    (channel,) = _channels()

    sweep.start_next(channel)

    (pin,) = pinterest.pins
    (post,) = store.repository().recent([channel.id], 1)
    model = DANE if post.preset == "dane" else CLARK
    assert pin.body["description"] == ""
    assert pin.body["link"] == ""
    assert any(
        f'"pin_title":"{prefix} {model.instagram[0]}"' in pin.body["story_pin"]
        for prefix in ("ig:", "insta:")
    )
    assert (post.link, post.description) == ("", "")


def test_about_half_the_pins_are_ig_pins(monkeypatch):
    monkeypatch.setenv("OFM_DISTRIBUTION_HANDLE_SHARE", "0.5")

    titles = [sweep._what(DANE, "https://x").title for _ in range(400)]

    ig = sum(1 for t in titles if t != DANE.title)
    assert 140 <= ig <= 260


def test_the_page_says_it_works_and_shows_pins_and_clicks(
    pinterest,
    drive,
    pictures,
    monkeypatch,
):
    monkeypatch.setenv("OFM_DISTRIBUTION_SCHEDULED", "true")
    _add("one")
    (channel,) = _channels()
    sweep.start_next(channel)

    page = _signed_in().get("/distribution")

    assert page.status_code == 200
    assert "Working: 1 of 1 accounts pinning" in page.text
    assert "Pins today" in page.text
    assert "Link clicks today" in page.text


def test_the_page_names_an_account_that_needs_new_cookies(pinterest, drive, pictures):
    _add("one")
    (channel,) = _channels()
    pinterest.logged_out = True
    sweep.start_next(channel)

    page = _signed_in().get("/distribution")

    assert "Need new cookies (logged out): @pin_one" in page.text


def test_the_report_has_each_models_pins_and_link_clicks_per_day(
    pinterest,
    drive,
    pictures,
):
    _add("one")
    (channel,) = _channels()
    for _ in range(5):
        sweep.start_next(channel)
    dane_link = next(
        m
        for m in store.repository().model_links(users.agency_id())
        if m.preset == "dane"
    )
    for kind in ("view", "view", "click"):
        self_links.record_click(
            SelfClick(link_id=dane_link.link_id, kind=kind, referrer="pinterest.com"),
        )

    found = report(7)

    dane = next(m for m in found.models if m.model == "dane")
    clark = next(m for m in found.models if m.model == "clark")
    assert (dane.pins, dane.link_views, dane.link_clicks) == (4, 2, 1)
    assert (clark.pins, clark.link_views) == (1, 0)
    assert dane.views_per_linked_pin == 0.5
    (account,) = found.accounts
    assert (account.account, account.pins) == ("pin_one", 5)


def test_the_page_names_an_account_whose_pins_all_fail(pinterest, drive, pictures):
    _add("one")
    (channel,) = _channels()
    refusal = {
        "resource_response": {
            "status": "failure",
            "error": {"message": "Board is archived", "code": 0, "http_status": 400},
        },
    }
    pinterest.fail_next = [FakeReply.json_body(refusal, status=400)] * 20

    sweep.start_next(channel)
    page = _signed_in().get("/distribution")

    assert "Every pin failed in the last 24 h: @pin_one" in page.text
    assert "Board is archived" in page.text


def test_the_page_warns_a_week_before_an_accounts_cookies_expire(pinterest, drive):
    soon = int(time.time()) + 3 * 86400
    expiring = cookies_txt("one").replace(
        b"1999999999\t_pinterest_sess",
        f"{soon}\t_pinterest_sess".encode(),
    )
    _dist().add([Upload(name="one.txt", file=io.BytesIO(expiring))])

    page = _signed_in().get("/distribution")

    assert "Cookies expire within 7 days, upload new ones: @pin_one" in page.text


def test_after_a_pin_the_accounts_cookies_are_saved_again(pinterest, drive, pictures):
    _add("one")
    (channel,) = _channels()

    sweep.start_next(channel)

    after = store.repository().channel(channel.id)
    assert after is not None
    assert after.cookies_expire_at == 1999999999.0
    assert "_pinterest_sess" in store.repository().cookies(channel.id)


def test_a_job_for_a_post_no_longer_claimed_pins_nothing(pinterest, drive, pictures):
    _add("one")
    (channel,) = _channels()
    post = store.repository().next_post(channel, 3)
    assert post is not None

    with pytest.raises(NotFoundError):
        publish.publish_post(post_id=post.id)

    assert pinterest.pins == []


def test_a_file_pinterest_cannot_process_is_not_tried_again(pinterest, drive, pictures):
    _add("one")
    (channel,) = _channels()
    pinterest.broken_uploads = True

    sweep.start_next(channel)

    (post,) = store.repository().recent([channel.id], 1)
    assert post.status is PostStatus.failed
    following = store.repository().next_post(channel, 3)
    assert following is not None
    assert following.id != post.id


def test_an_account_whose_pins_keep_failing_waits(pinterest, drive, pictures):
    _add("one")
    (channel,) = _channels()
    refusal = {
        "resource_response": {
            "status": "failure",
            "error": {"message": "Account on hold", "code": 0, "http_status": 400},
        },
    }
    pinterest.fail_next = [FakeReply.json_body(refusal, status=400)] * 50

    for _ in range(3):
        sweep.start_next(channel)

    after = store.repository().channel(channel.id)
    assert after is not None
    assert after.resume_at is not None
    assert after.resume_at > time.time()


def test_a_file_another_account_is_pinning_counts_as_taken(pinterest, drive):
    _add("one", "two")
    one, two = _channels()
    taken = store.repository().next_post(one, 3)
    assert taken is not None
    assert store.repository().claim(taken.id, "t", "", "")

    for _ in range(10):
        following = store.repository().next_post(two, 3)
        assert following is not None
        assert following.file_id != taken.file_id


def test_a_post_whose_job_still_runs_is_not_cut_off(pinterest, drive):
    _add("one")
    (channel,) = _channels()
    post = store.repository().next_post(channel, 3)
    assert post is not None
    assert store.repository().claim(post.id, "t", "", "")
    store.repository().set_job(post.id, "job-alive")

    alive = store.repository().cut_off(-60, "cut", {"job-alive"})
    dead = store.repository().cut_off(-60, "cut", set())

    assert (alive, dead) == (0, 1)


def test_the_sweep_never_makes_a_threads_folder(pinterest, drive):
    _add("one")

    (bob,) = drive.named("bob.ig - Anna", ROOT)
    assert drive.named("THREADS", bob) == []
