"""/clone-links in a real browser, every outside service faked (see
conftest.py): Check cleans or refuses the paste, Generate's dialog prices
each link from the preview stream, Remove leaves a link out of what is
sent, a good reel lands in the VA's own GOOD REELS, Stop stops a Generate,
nothing is asked without Google Drive, and an open AI decision stays open
while its card is polled."""

import re
import time
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path

import pytest
from e2e_session import Session, connect_google, my_root
from playwright.sync_api import Locator, Request, Response, expect
from sqlalchemy import create_engine, text

from ofmhelpers.aigenproviders.kaiai.fake import FAKE_KEY as KIE_FAKE_KEY
from ofmhelpers.gdrive.fake import FakeDrive
from ofmhelpers.web.clone_links.links import NOT_A_VIDEO_SITE
from ofmhelpers.web.clone_links.preview import CANNOT_OPEN
from ofmhelpers.web.clone_links.review import NOT_CONNECTED

pytestmark = pytest.mark.e2e

GOOGLE = "va.google@example.com"
# A fake reel is generated in ~1 s plus the poll's backoff.
RESULT_TIMEOUT_MS = 30_000
# The page asks /clone-links/reviews every 4 s while a block is unsettled.
REVIEW_POLL_TIMEOUT_MS = 15_000
PRICE = re.compile(r"^\$\d+\.\d\d$")
# What the page sends once Generate is pressed (live-gallery.js's own
# /gallery/latest poll is not one of them).
ASKS = re.compile(r"/clone-links/(?:check|previews|run)$")


def _open(va: Session, subject: Path, api_key: str) -> None:
    """/clone-links with kie.ai, `api_key` and one subject picked."""
    assert va.goto("/clone-links") == 200
    va.page.select_option("#provider-select", "kie")
    va.page.fill("#api-key", api_key)
    va.page.locator('.file-picker[data-field="image"] .file-add-input').set_input_files(
        str(subject)
    )


def _generate(va: Session) -> Locator:
    """Presses Generate: the confirm dialog's tiles."""
    va.page.click('#clone-links-form button[type="submit"]')
    expect(va.page.locator("#clone-links-confirm")).to_be_visible()
    return va.page.locator("#clone-links-confirm-lines .pick-tile")


def _tile(va: Session, url: str) -> Locator:
    return va.page.locator("#clone-links-confirm-lines .pick-tile").filter(
        has=va.page.locator(f'.pick-cover[href="{url}"]')
    )


def _confirm(va: Session) -> tuple[Request, Response]:
    """Presses the dialog's Generate: the /run request sent and its answer."""
    run = va.page.locator("#clone-links-confirm-go")
    with (
        va.page.expect_request(lambda r: r.url.endswith("/clone-links/run")) as sent,
        va.page.expect_response(lambda r: r.url.endswith("/clone-links/run")) as answer,
    ):
        run.click()
    assert answer.value.ok, answer.value.text()
    return sent.value, answer.value


def _form_fields(request: Request) -> dict[str, list[str]]:
    """The multipart form's text fields (files left out), each name's values
    in the order sent."""
    head = f"Content-Type: {request.header_value('content-type')}\r\n\r\n"
    message = BytesParser(policy=HTTP).parsebytes(
        head.encode() + (request.post_data_buffer or b"")
    )
    fields: dict[str, list[str]] = {}
    for part in message.iter_parts():
        if part.get_filename() is None:
            name = part.get_param("name", header="content-disposition")
            fields.setdefault(str(name), []).append(part.get_content())
    return fields


def _card(va: Session, job_id: str) -> Locator:
    return va.page.locator(f'#clone-links-gallery > [data-job-id="{job_id}"]')


def _ready_tiles(tiles: Locator, count: int) -> None:
    """Waits until `count` tiles are priced (their preview line came)."""
    expect(tiles).to_have_count(count)
    for i in range(count):
        expect(tiles.nth(i).locator(".pick-price")).to_have_text(
            PRICE, timeout=RESULT_TIMEOUT_MS
        )


def _run_good_reel(va: Session, link: str) -> str:
    """Generates `link` with the fake's own key: its job, once its reel is in
    GOOD REELS."""
    va.page.fill("#clone-links-text", link)
    _ready_tiles(_generate(va), 1)
    _request, run = _confirm(va)
    [job_id] = run.json()["job_ids"]
    expect(_card(va, job_id).locator(".ai-check")).to_contain_text(
        "In Drive: GOOD REELS", timeout=2 * RESULT_TIMEOUT_MS
    )
    return job_id


def test_clone_links_checks_prices_runs_and_files_a_good_reel(
    va: Session, fake_drive: FakeDrive, subject_images: list[Path]
):
    """Check refuses a paste with bad lines (they go to the top of the box,
    selected) and cleans a good one; Generate prices the reel from its
    preview and leaves the dead one out; the reel sent is checked GOOD and
    lands in the VA's own GOOD REELS."""
    connect_google(va, GOOGLE)
    _open(va, subject_images[0], KIE_FAKE_KEY)
    box = va.page.locator("#clone-links-text")
    reel = "https://www.instagram.com/reel/E2ECL1/"
    dead = "https://www.instagram.com/reel/fake-fail-2/"
    junk = "https://claude.ai/new"
    box.fill(f"{reel}\n{dead}{reel}\n{junk}")
    va.page.click(".check-links-btn")

    refused = va.page.locator("#clone-links-refused")
    expect(refused).to_be_visible()
    expect(refused.locator("h2")).to_have_text("Please put one link per line")
    expect(refused.locator("strong.cost-line-name")).to_have_text(
        [f"{dead}{reel}", junk]
    )
    expect(refused.locator(".cost-line-detail")).to_have_text(
        ["2 links on one line", NOT_A_VIDEO_SITE]
    )
    expect(va.page.locator("#clone-links-refused-note")).to_be_visible()
    refused.get_by_role("button", name="OK").click()
    expect(refused).to_be_hidden()
    bad_block = f"{dead}{reel}\n{junk}"
    expect(box).to_have_value(f"{bad_block}\n{reel}")
    expect(box).to_be_focused()
    assert box.evaluate("b => [b.selectionStart, b.selectionEnd]") == [
        0,
        len(bad_block),
    ]

    box.fill(f"{reel}?igsh=abc\nhttps://instagram.com/reels/E2ECL1\n{dead}")
    va.page.click(".check-links-btn")
    expect(box).to_have_value(f"{reel}\n{dead}")

    tiles = _generate(va)
    expect(tiles).to_have_count(2)
    ready, left_out = tiles.nth(0), tiles.nth(1)
    expect(ready.locator(".pick-cover")).to_have_attribute("href", reel)
    expect(ready.locator(".pick-len")).to_have_text(
        re.compile(r"^\d+\.\d s$"), timeout=RESULT_TIMEOUT_MS
    )
    expect(ready.locator(".pick-price")).to_have_text(PRICE)
    expect(ready.locator(".pick-cover .spinner")).to_have_count(0)
    expect(left_out.locator(".pick-cover")).to_have_attribute("href", dead)
    expect(left_out).to_have_attribute("data-problem", "", timeout=RESULT_TIMEOUT_MS)
    expect(left_out.locator(".pick-why")).to_have_text(CANNOT_OPEN)
    expect(left_out.locator(".pick-price")).to_have_text("")
    expect(va.page.locator("#clone-links-confirm-title")).to_have_text(
        "Generate 1 clone?"
    )
    price = ready.locator(".pick-price").inner_text()
    expect(va.page.locator("#clone-links-confirm-usd")).to_have_text(price)
    expect(va.page.locator("#clone-links-confirm-go")).to_have_text(
        f"Generate 1 · {price}"
    )

    request, run = _confirm(va)

    fields = _form_fields(request)
    assert fields["links"] == [reel]
    assert fields["batch"] == [run.json()["batch"]]
    assert re.fullmatch(r"[0-9a-f]{16}", run.json()["batch"])
    [job_id] = run.json()["job_ids"]
    review = _card(va, job_id).locator(".ai-check")
    expect(review).to_contain_text(
        "In Drive: GOOD REELS", timeout=2 * RESULT_TIMEOUT_MS
    )
    expect(review.locator(".why-toggle")).to_have_text("AI decision: good")
    (good,) = fake_drive.named("GOOD REELS", my_root(fake_drive, GOOGLE))
    assert len(fake_drive.names_in(good)) == 1
    # The refused Check's 400, which Chromium logs; nothing else.
    assert va.js_errors == [
        (
            "Failed to load resource: the server responded with a status of 400"
            " (Bad Request)"
        )
    ]


@pytest.mark.usefixtures("fake_drive")
def test_a_removed_tile_is_left_out_of_what_generate_sends(
    va: Session, subject_images: list[Path]
):
    """Remove takes a link out of the dialog: the title, the total and the
    button follow, and /run gets exactly the link left. (A wrong key: the
    run fails at once, nothing generated.)"""
    connect_google(va, GOOGLE)
    _open(va, subject_images[0], "e2e-wrong-key")
    kept = "https://www.instagram.com/reel/E2EKEEP/"
    removed = "https://www.instagram.com/reel/E2EDROP/"
    va.page.fill("#clone-links-text", f"{kept}\n{removed}")
    with va.page.expect_response(lambda r: r.url.endswith("/previews")) as previews:
        tiles = _generate(va)
    _ready_tiles(tiles, 2)
    title = va.page.locator("#clone-links-confirm-title")
    total = va.page.locator("#clone-links-confirm-usd")
    go = va.page.locator("#clone-links-confirm-go")
    one = _tile(va, kept).locator(".pick-price").inner_text()
    expect(title).to_have_text("Generate 2 clones?")
    expect(total).to_have_text(f"${2 * float(one[1:]):.2f}")
    expect(go).to_be_enabled()
    # The tiles are sorted once the stream ends, and a Remove within 400 ms
    # of that is ignored (it may be aimed at a tile that just moved).
    previews.value.finished()
    va.page.wait_for_timeout(500)

    _tile(va, removed).locator(".pick-remove").click()

    expect(tiles).to_have_count(1)
    expect(_tile(va, kept)).to_have_count(1)
    expect(title).to_have_text("Generate 1 clone?")
    expect(total).to_have_text(one)
    expect(go).to_have_text(f"Generate 1 · {one}")
    request, run = _confirm(va)
    assert _form_fields(request)["links"] == [kept]
    [job_id] = run.json()["job_ids"]
    expect(_card(va, job_id).locator(".result-failed")).to_have_text(
        re.compile("Wrong API key"), timeout=RESULT_TIMEOUT_MS
    )
    assert va.js_errors == []


def test_generate_without_google_drive_asks_the_server_nothing(
    va: Session, subject_images: list[Path]
):
    """No Drive to put the reels in: Generate says so at once -- no Check,
    no preview read, no dialog, nothing sent."""
    _open(va, subject_images[0], KIE_FAKE_KEY)
    expect(va.page.locator(".drive-connect")).to_be_visible()
    va.page.fill("#clone-links-text", "https://www.instagram.com/reel/E2ENODRIVE/")
    asked: list[str] = []
    va.page.on("request", lambda r: ASKS.search(r.url) and asked.append(r.url))

    va.page.click('#clone-links-form button[type="submit"]')

    expect(va.page.locator("#clone-links-form .generation-error")).to_have_text(
        NOT_CONNECTED
    )
    expect(va.page.locator("#clone-links-confirm")).to_be_hidden()
    assert asked == []
    assert va.js_errors == []


@pytest.mark.usefixtures("fake_drive")
def test_stop_after_queueing_stops_that_generate(
    va: Session, subject_images: list[Path]
):
    """Stop shows next to "Queued N runs." and stops that Generate: it posts
    the first run's stop, the server names the batch /run made."""
    connect_google(va, GOOGLE)
    _open(va, subject_images[0], "e2e-wrong-key")
    links = [
        "https://www.instagram.com/reel/E2ESTOP1/",
        "https://www.instagram.com/reel/E2ESTOP2/",
    ]
    va.page.fill("#clone-links-text", "\n".join(links))
    _ready_tiles(_generate(va), 2)
    _request, run = _confirm(va)
    queued = run.json()
    status = va.page.locator("#clone-links-form .generation-status")
    stop = va.page.locator("#clone-links-stop")
    expect(status).to_have_text("Queued 2 runs.")
    expect(stop).to_be_visible()

    with va.page.expect_response(lambda r: "/clone-links/stop/" in r.url) as answer:
        stop.click()

    assert answer.value.url.endswith(f"/clone-links/stop/{queued['job_ids'][0]}")
    assert answer.value.ok, answer.value.text()
    assert answer.value.json()["batch"] == queued["batch"]
    expect(status).to_have_text("Stopped: runs not started yet won't run.")
    expect(stop).to_be_hidden()
    for job_id in queued["job_ids"]:  # every run ends, none left running
        expect(_card(va, job_id).locator(".result-failed")).to_have_count(
            1, timeout=RESULT_TIMEOUT_MS
        )
    assert va.js_errors == []


@pytest.mark.usefixtures("fake_drive")
def test_an_open_ai_decision_stays_open_while_its_card_is_polled(
    va: Session, subject_images: list[Path], e2e_backend
):
    """While a reel uploads its block is polled every few seconds and drawn
    again: the AI decision the VA opened stays open (it closed every 4 s)."""
    connect_google(va, GOOGLE)
    _open(va, subject_images[0], KIE_FAKE_KEY)
    job_id = _run_good_reel(va, "https://www.instagram.com/reel/E2EOPEN/")
    # Back to "uploading" (checked, not in Drive yet): what the page polls.
    now = time.time()
    with create_engine(e2e_backend.database_url).begin() as conn:
        conn.execute(
            text(
                "UPDATE clone_from_links SET status = 'uploading', drive_url = NULL,"
                " drive_file_id = NULL, finished_at = NULL, created_at = :now,"
                " started_at = :now WHERE job_id = :job"
            ),
            {"now": now, "job": job_id},
        )
    assert va.goto("/clone-links") == 200
    review = _card(va, job_id).locator(".ai-check")
    expect(review.locator('[data-status="uploading"]')).to_be_visible()
    toggle = review.locator(".why-toggle")
    panel = review.locator(".ai-check-why")

    toggle.click()
    expect(panel).to_have_js_property("hidden", value=False)
    # The second answer is asked for only after the first was drawn.
    for _ in range(2):
        with va.page.expect_response(
            lambda r: "/clone-links/reviews?" in r.url, timeout=REVIEW_POLL_TIMEOUT_MS
        ) as polled:
            pass
        assert polled.value.ok
        [state] = polled.value.json()
        assert state["job_id"] == job_id
        assert state["settled"] is False

    expect(panel).to_have_js_property("hidden", value=False)
    expect(toggle).to_have_attribute("aria-expanded", "true")
    assert va.js_errors == []
