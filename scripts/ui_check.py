import argparse
import fnmatch
import os
import re
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import requests
from PIL import Image
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, JsonValue
from ui_screens import WIDTHS, Shot, shoot, sign_in
lazy from playwright.sync_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Dialog,
    Page,
    Response,
    Route,
    WebSocket,
    WebSocketRoute,
)

OUT = Path("reports/ui-check")
FAKE_JOB = "ui-check-job"
STATUS_GLOB = f"**/jobs/{FAKE_JOB}/status"
SOCKET_GLOB = "**/notifications/ws"
WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ACTION_MS = 15_000
POLL_MS = 200
ANSWER_MS = 10_000
BACK_MS = 8_000
CONFIRM_MS = 3_000
SLOW_MS = 8_000
SLOW_SAMPLE_MS = 4_000
CLICKS = 50
CLICK_MS = 1_000
CLICK_GAP_MS = 100
SETTLE_MS = 2_000
SHOT_PX = 2_400
REAL_ANSWER_MS = 60_000
REAL_DOWNLOAD_MS = 120_000
REAL_SPOOF_MS = 300_000
HEALTH_S = 5
ACCEPTED = 202
REFUSED = 400
LINE_CHARS = 500
NETWORK = "Failed to load resource"
OFFLINE = "ERR_INTERNET_DISCONNECTED"
RAW = re.compile(
    r"JSON|undefined|\bnull\b|NaN|\[object|Traceback|Exception|SyntaxError"
    r"|TypeError|Unexpected token|<[a-zA-Z!/]|XHR"
)
TEXT_TYPES = ("text", "search", "url", "email", "password", "tel", "number")
FIELDS = ", ".join(
    ("textarea", "input:not([type])", *(f"input[type={t}]" for t in TEXT_TYPES))
)
DOWNLOAD_LINKS = "#dl-urls"
SPOOF_PICKED = '.file-picker[data-field="video"] li'
VISIBLE_TEXT = """(els) => els
    .filter((el) => el.checkVisibility())
    .map((el) => el.innerText.replace(/\\s+/g, " ").trim())
    .filter(Boolean)
    .join(" | ")"""
FIRST_CARD_TEXT = """(els) =>
    (els.find((el) => el.checkVisibility())?.innerText || "")
        .replace(/\\s+/g, " ").trim()"""
DOWNLOADED_LINK = """(cards) => {
    for (const card of cards) {
        if (card.dataset.task !== "download_media") continue;
        if (!card.querySelector("video.result-video")) continue;
        try {
            const urls = JSON.parse(card.dataset.params || "{}").urls || [];
            if (urls.length === 1) return urls[0];
        } catch {
            continue;
        }
    }
    return "";
}"""
SPOOF_SOURCE = """(cards) => {
    for (const card of cards) {
        if (!card.querySelector(".download-btn")) continue;
        try {
            const source = JSON.parse(card.dataset.params || "{}").source;
            if (source) return source;
        } catch {
            continue;
        }
    }
    return "";
}"""
SETTLED = """(cards, id) => {
    const first = cards[0];
    return !!first && first.dataset.jobId === id
        && !!first.querySelector(".download-btn, .result-failed");
}"""
CLONE_CARD = (
    f'<div class="result-item" data-job-id="{FAKE_JOB}" data-pending'
    ' data-poll-prefix="/clone" data-poll-kind="video">'
    '<div class="result-file"><span class="spinner"></span> generating</div>'
    '<p class="source">Clone</p></div>'
)


class Fill(BaseModel, frozen=True):
    selector: str
    text: str
    only_if_empty: bool = False


class Pick(BaseModel, frozen=True):
    selector: str
    kind: Literal["image", "video"]


class PageForm(BaseModel, frozen=True):
    path: str
    submit: str = ""
    post: str = ""
    status: str = ".generation-status, [role=status], [role=alert]"
    gallery: str = "#results-panel .results"
    confirm: str | None = None
    answer: dict[str, JsonValue] = {"job_id": FAKE_JOB, "job_ids": [FAKE_JOB]}
    fills: tuple[Fill, ...] = ()
    picks: tuple[Pick, ...] = ()
    real: Literal["download", "spoof"] | None = None

    @property
    def can_submit(self) -> bool:
        return bool(self.submit and self.post)


API_KEY = Fill(selector="#api-key", text="ui-check-key", only_if_empty=True)
SUBJECT = Pick(
    selector='.file-picker[data-field="image"] .file-add-input', kind="image"
)
ONE_VIDEO = Pick(
    selector='.file-picker[data-field="video"] .file-add-input', kind="video"
)
PAGES: frozendict[str, PageForm] = frozendict(
    {
        "/download-assets": PageForm(
            path="/download-assets",
            submit='#download-assets-form button[type="submit"]',
            post="**/download-media/run",
            status="#results-panel .generation-status",
            answer={"job_id": FAKE_JOB, "job_ids": [FAKE_JOB], "again": []},
            fills=(
                Fill(
                    selector=DOWNLOAD_LINKS,
                    text="https://www.example.com/ui-check/video.mp4",
                ),
            ),
            real="download",
        ),
        "/spoof": PageForm(
            path="/spoof",
            submit='#repurpose-form button[type="submit"]',
            post="**/spoof/run",
            status="#results-panel .generation-status",
            gallery="#rp-gallery",
            picks=(ONE_VIDEO,),
            real="spoof",
        ),
        "/clone": PageForm(
            path="/clone",
            submit='#clone-form button[type="submit"]',
            post="**/clone/run",
            status="#clone-form .generation-status",
            gallery="#clone-gallery",
            confirm="#clone-confirm-go",
            answer={"job_ids": [FAKE_JOB], "html": CLONE_CARD},
            fills=(API_KEY,),
            picks=(
                SUBJECT,
                Pick(
                    selector='.file-picker[data-field="videos"] .file-add-input',
                    kind="video",
                ),
            ),
        ),
        "/clone-long": PageForm(
            path="/clone-long",
            submit='#clone-long-form button[type="submit"]',
            post="**/clone-long/run",
            status="#results-panel .generation-status",
            gallery="#clone-long-gallery",
            answer={"job_id": FAKE_JOB},
            fills=(API_KEY,),
            picks=(SUBJECT, ONE_VIDEO),
        ),
        "/generate": PageForm(
            path="/generate",
            submit='#generate-form button[type="submit"]',
            post="**/ai/run",
            status="#results-panel .generation-status",
            fills=(
                API_KEY,
                Fill(
                    selector='#generate-form textarea[name="prompt"]', text="ui check"
                ),
            ),
        ),
    }
)


class Junk(BaseModel, frozen=True):
    name: str
    text: str
    number: str


JUNK = (
    Junk(name="10k", text="x" * 10_000, number="99999999999999999999"),
    Junk(
        name="emoji",
        text="\U0001f600\U0001f525\U0001f469\u200d\U0001f467 " * 50,
        number="-1",
    ),
    Junk(
        name="rtl",
        text="\u0645\u0631\u062d\u0628\u0627 \u202eabc\u202c \u05e9\u05dc\u05d5\u05dd",
        number="0",
    ),
    Junk(
        name="script",
        text='<script>alert("x")</script><img src=x onerror=alert(1)>',
        number="1.5",
    ),
    Junk(name="empty", text="", number=""),
)


class Reply(BaseModel, frozen=True):
    status: int
    content_type: str
    body: str

    def fulfill(self, route: Route) -> None:
        route.fulfill(
            status=self.status, content_type=self.content_type, body=self.body
        )


CRASH = Reply(
    status=500,
    content_type="application/json",
    body='{"detail": "Something went wrong. Try again."}',
)
HTML = Reply(
    status=200,
    content_type="text/html; charset=utf-8",
    body="<!doctype html><title>Sign in</title><h1>Sign in</h1>",
)


class Queued(BaseModel):
    job_id: str | None = None
    job_ids: list[str] = []
    again: list[str] = []

    @property
    def ids(self) -> list[str]:
        named = [*self.job_ids, *self.again, *([self.job_id] if self.job_id else [])]
        return list(dict.fromkeys(named))


class Media(BaseModel, frozen=True):
    image: Path
    video: Path


class Failure(BaseModel, frozen=True):
    text: str
    url: str


class Step(BaseModel):
    name: str = ""
    done: str = ""
    problems: list[str] = []
    requests: int | None = None
    keys: int | None = None
    message: str = ""
    card: str = ""
    console: list[str] = []
    shots: list[str] = []

    @property
    def passed(self) -> bool:
        return not self.problems

    @property
    def detail(self) -> str:
        return "; ".join(self.problems) if self.problems else self.done

    def row(self) -> str:
        cells = (
            self.name,
            "pass" if self.passed else "**FAIL**",
            counted(self.requests),
            counted(self.keys),
            self.detail,
        )
        return "| " + " | ".join(cell(c) for c in cells) + " |"

    def section(self) -> list[str]:
        lines = [
            "",
            f"## {self.name}",
            "",
            f"Result: {'pass' if self.passed else 'FAIL'}",
        ]
        if self.message:
            lines.append(f"Message shown: {self.message}")
        if self.card:
            lines.append(f"First card says: {self.card}")
        lines += [f"- {problem}" for problem in self.problems]
        if self.console:
            lines += ["", "Console:", "```", *self.console, "```"]
        lines += ["", *(f"![{shot}]({shot})" for shot in self.shots)]
        return lines


class Report(BaseModel):
    page: str
    base_url: str
    stamp: str
    steps: list[Step]

    @property
    def passed(self) -> bool:
        return all(step.passed for step in self.steps)

    def markdown(self) -> str:
        failed = sum(not step.passed for step in self.steps)
        verdict = "PASS" if self.passed else "FAIL"
        lines = [
            f"# UI check {self.page}",
            "",
            f"- App: {self.base_url}",
            f"- Run: {self.stamp} UTC",
            f"- Result: {verdict} ({failed} of {len(self.steps)} steps failed)",
            "",
            "| Step | Result | Requests | Keys | Detail |",
            "|---|---|---|---|---|",
            *(step.row() for step in self.steps),
        ]
        for step in self.steps:
            lines += step.section()
        return "\n".join(lines) + "\n"


class Args(BaseModel, frozen=True):
    path: str
    submit: str | None = None
    post: str | None = None
    real: bool = False


def counted(value: int | None) -> str:
    return "-" if value is None else str(value)


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def clipped(text: str) -> str:
    return (
        text if len(text) <= LINE_CHARS else f"{text[:LINE_CHARS]}… ({len(text)} chars)"
    )


def first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0] if lines else ""


def plain(message: str) -> list[str]:
    if not message:
        return ["no message shown"]
    if raw := RAW.search(message):
        return [f"the message is not plain words ({raw.group()}): {message!r}"]
    return []


def never(_failure: Failure) -> bool:
    return False


def within(page: Page, ms: int, check: Callable[[], bool]) -> bool:
    waited = 0
    while not check():
        if waited >= ms:
            return False
        page.wait_for_timeout(POLL_MS)
        waited += POLL_MS
    return True


class Watch:
    def __init__(self, context: BrowserContext, *, fake_sockets: bool = True) -> None:
        self.page = context.new_page()
        self.errors: list[str] = []
        self.console: list[str] = []
        self.network: list[Failure] = []
        self.dialogs: list[str] = []
        self.documents: list[str] = []
        self.sockets: list[str] = []
        self.lines: list[str] = []
        self.page.on("pageerror", self._on_error)
        self.page.on("console", self._on_console)
        self.page.on("dialog", self._on_dialog)
        self.page.on("response", self._on_response)
        if fake_sockets:
            self.page.route_web_socket(SOCKET_GLOB, self._fake_socket)
            self.page.on("websocket", self._on_socket)

    def _fake_socket(self, socket: WebSocketRoute) -> None:
        self.lines.append(clipped(f"[socket] faked {socket.url}, never connected"))

    def _on_socket(self, socket: WebSocket) -> None:
        self.sockets.append(socket.url)
        self.lines.append(clipped(f"[socket] reached the server: {socket.url}"))

    def _on_error(self, error: PlaywrightError) -> None:
        self.errors.append(str(error))
        self.lines.append(clipped(f"[pageerror] {error}"))

    def _on_console(self, message: ConsoleMessage) -> None:
        if message.type != "error":
            return
        url = message.location.get("url", "")
        if message.text.startswith(NETWORK):
            self.network.append(Failure(text=message.text, url=url))
        else:
            self.console.append(message.text)
        self.lines.append(clipped(f"[console] {message.text} ({url})"))

    def _on_dialog(self, dialog: Dialog) -> None:
        self.dialogs.append(f"{dialog.type}: {dialog.message}")
        self.lines.append(clipped(f"[dialog] {dialog.type}: {dialog.message}"))
        dialog.dismiss()

    def _on_response(self, response: Response) -> None:
        if response.request.resource_type == "document" and response.status >= REFUSED:
            self.documents.append(f"{response.status} {response.url}")

    def problems(self, expected: Callable[[Failure], bool]) -> list[str]:
        return [
            *(clipped(f"page error: {error}") for error in self.errors),
            *(clipped(f"console error: {line}") for line in self.console),
            *(
                f"failed request: {failure.text} ({failure.url})"
                for failure in self.network
                if not expected(failure)
            ),
            *(clipped(f"dialog popped up: {dialog}") for dialog in self.dialogs),
            *(f"the page answered {document}" for document in self.documents),
            *(f"a WebSocket reached the server: {url}" for url in self.sockets),
        ]


type Verdict = Literal["answer", "running", "abort", "pass"]


def verdict(method: str, url: str, post: str) -> Verdict:
    if fnmatch.fnmatchcase(url, STATUS_GLOB):
        return "running"
    if method == "POST" and fnmatch.fnmatchcase(url, post):
        return "answer"
    return "abort" if method in WRITES else "pass"


class Mock:
    def __init__(
        self, context: BrowserContext, post: str, reply: Callable[[Route], None]
    ) -> None:
        self.post = post
        self.reply = reply
        self.keys: list[str] = []
        self.blocked: list[str] = []
        context.route("**/*", self._route)

    def _route(self, route: Route) -> None:
        request = route.request
        match verdict(request.method, request.url, self.post):
            case "answer":
                self.keys.append(request.headers.get("idempotency-key", ""))
                self.reply(route)
            case "running":
                route.fulfill(json={"status": "running"})
            case "abort":
                self.blocked.append(f"{request.method} {request.url}")
                route.abort("blockedbyclient")
            case "pass":
                route.fallback()

    def expected(self, failure: Failure) -> bool:
        return fnmatch.fnmatchcase(failure.url, self.post)

    def offline_expected(self, failure: Failure) -> bool:
        return self.expected(failure) or OFFLINE in failure.text

    def problems(self) -> list[str]:
        return [
            f"blocked a write that would reach the server: {b}" for b in self.blocked
        ]


class Check:
    def __init__(
        self,
        browser: Browser,
        base_url: str,
        state: Path,
        form: PageForm,
        out: Path,
        media: Media | None,
    ) -> None:
        self.browser = browser
        self.base_url = base_url
        self.state = state
        self.form = form
        self.out = out
        self.media = media

    def context(self) -> BrowserContext:
        context = self.browser.new_context(
            base_url=self.base_url,
            storage_state=self.state,
            viewport={"width": WIDTHS[0], "height": 900},
        )
        context.set_default_timeout(ACTION_MS)
        return context

    def prepare(self, watch: Watch) -> None:
        watch.page.goto(self.form.path, wait_until="load")
        watch.page.locator(self.form.submit).wait_for(state="visible")
        for pick in self.form.picks:
            watch.page.locator(pick.selector).set_input_files(self.file_for(pick))
        for fill in self.form.fills:
            field = watch.page.locator(fill.selector)
            if field.is_visible() and not (fill.only_if_empty and field.input_value()):
                field.fill(fill.text)

    def file_for(self, pick: Pick) -> Path:
        if self.media is None:
            msg = "no test media was made for the file pickers"
            raise RuntimeError(msg)
        return self.media.image if pick.kind == "image" else self.media.video

    def submit(self, page: Page) -> None:
        page.locator(self.form.submit).click()
        self.confirm(page, CONFIRM_MS)

    def confirm(self, page: Page, wait_ms: int, *, force: bool = False) -> None:
        if not self.form.confirm:
            return
        button = page.locator(self.form.confirm)
        if within(page, wait_ms, button.is_visible):
            button.click(force=force, timeout=CLICK_MS if force else ACTION_MS)

    def click(self, page: Page, missed: list[str]) -> None:
        try:
            page.locator(self.form.submit).click(force=True, timeout=CLICK_MS)
            self.confirm(page, 0, force=True)
        except PlaywrightError as exc:
            missed.append(first_line(exc.message))
        page.wait_for_timeout(CLICK_GAP_MS)

    def accept(self, route: Route) -> None:
        route.fulfill(status=ACCEPTED, json=self.form.answer)

    def message(self, page: Page) -> str:
        return str(page.locator(self.form.status).evaluate_all(VISIBLE_TEXT))

    def card(self, page: Page) -> str:
        cards = page.locator(f"{self.form.gallery} > .result-item")
        return str(cards.evaluate_all(FIRST_CARD_TEXT))

    def seen(self, page: Page) -> str:
        return " | ".join(
            text for text in (self.message(page), self.card(page)) if text
        )

    def button_back(self, page: Page) -> bool:
        return within(page, BACK_MS, page.locator(self.form.submit).is_enabled)

    def shot(self, page: Page, name: str) -> str:
        page.evaluate("window.scrollTo(0, 0)")
        height = int(page.evaluate("document.documentElement.scrollHeight"))
        page.screenshot(
            path=self.out / f"{name}.png",
            full_page=True,
            clip={"x": 0, "y": 0, "width": WIDTHS[0], "height": min(height, SHOT_PX)},
        )
        return f"{name}.png"

    def posted(self, response: Response) -> bool:
        return response.request.method == "POST" and fnmatch.fnmatchcase(
            response.url, self.form.post
        )

    def load(self) -> Step:
        context = self.context()
        try:
            watch = Watch(context)
            shoot(watch.page, Shot(name="load", path=self.form.path), self.out)
            problems = watch.problems(never)
            if "/login" in watch.page.url:
                problems.append("the signed-in admin was sent to /login")
            return Step(
                done=f"loaded at {' and '.join(map(str, WIDTHS))} px, no JS error",
                problems=problems,
                console=watch.lines,
                shots=[f"load-{width}.png" for width in WIDTHS],
            )
        finally:
            context.close()

    def answered(
        self, name: str, reply: Callable[[Route], None], *, offline: bool = False
    ) -> Step:
        context = self.context()
        try:
            mock = Mock(context, self.form.post, reply)
            watch = Watch(context)
            self.prepare(watch)
            if offline:
                context.set_offline(True)
            self.submit(watch.page)
            within(watch.page, ANSWER_MS, lambda: bool(self.message(watch.page)))
            message = self.message(watch.page)
            back = self.button_back(watch.page)
            shots = [self.shot(watch.page, name)]
            context.set_offline(False)
            problems = [*plain(message)]
            if not back:
                problems.append("the button stayed disabled")
            if not offline and not mock.keys:
                problems.append("the click sent no request")
            expected = mock.offline_expected if offline else mock.expected
            problems += [*mock.problems(), *watch.problems(expected)]
            return Step(
                done=f"shows {message!r}, the button is back, no JS error",
                problems=problems,
                requests=len(mock.keys),
                keys=len(set(mock.keys)),
                message=message,
                card=self.card(watch.page),
                console=watch.lines,
                shots=shots,
            )
        finally:
            context.close()

    def offline(self) -> Step:
        return self.answered(
            "offline",
            lambda route: route.abort("internetdisconnected"),
            offline=True,
        )

    def slow(self) -> Step:
        held: list[Route] = []
        context = self.context()
        try:
            mock = Mock(context, self.form.post, held.append)
            watch = Watch(context)
            self.prepare(watch)
            page = watch.page
            self.submit(page)
            asked = within(page, ANSWER_MS, lambda: bool(held))
            page.wait_for_timeout(SLOW_SAMPLE_MS)
            waiting = self.seen(page)
            shots = [self.shot(page, "slow-waiting")]
            page.wait_for_timeout(SLOW_MS - SLOW_SAMPLE_MS)
            for route in held:
                self.accept(route)
            back = self.button_back(page)
            page.wait_for_timeout(SETTLE_MS)
            after = self.seen(page)
            shots.append(self.shot(page, "slow"))
            problems = [] if asked else ["the click sent no request"]
            problems += [f"while waiting: {p}" for p in plain(waiting)]
            if not back:
                problems.append("the button stayed disabled after the answer")
            problems += [*mock.problems(), *watch.problems(mock.expected)]
            return Step(
                done=(
                    f"answered after {SLOW_MS // 1000} s; while waiting {waiting!r};"
                    f" after {after!r}"
                ),
                problems=problems,
                requests=len(mock.keys),
                keys=len(set(mock.keys)),
                message=waiting,
                console=watch.lines,
                shots=shots,
            )
        finally:
            context.close()

    def clicks(self) -> Step:
        context = self.context()
        try:
            mock = Mock(context, self.form.post, self.accept)
            watch = Watch(context)
            self.prepare(watch)
            missed: list[str] = []
            for _ in range(CLICKS):
                self.click(watch.page, missed)
            watch.page.wait_for_timeout(SETTLE_MS)
            keys = set(mock.keys)
            problems = [] if mock.keys else [f"{CLICKS} clicks sent no request"]
            if "" in keys:
                problems.append("a request went without an Idempotency-Key")
            if len(keys - {""}) > 1:
                problems.append(f"{len(keys)} different Idempotency-Key values, want 1")
            problems += [*mock.problems(), *watch.problems(mock.expected)]
            return Step(
                done=(
                    f"{CLICKS} clicks ({len(missed)} on a busy page) sent"
                    f" {len(mock.keys)} requests with one Idempotency-Key"
                ),
                problems=problems,
                requests=len(mock.keys),
                keys=len(keys),
                console=watch.lines,
                shots=[self.shot(watch.page, "clicks")],
            )
        finally:
            context.close()

    def fill_junk(self, page: Page, junk: Junk) -> int:
        form = page.locator(self.form.submit).locator("xpath=ancestor::form[1]")
        fields = form.locator(FIELDS)
        filled = 0
        for index in range(fields.count()):
            field = fields.nth(index)
            if not (field.is_visible() and field.is_editable()):
                continue
            number = field.get_attribute("type") == "number"
            field.fill(junk.number if number else junk.text)
            filled += 1
        return filled

    def junk_one(self, context: BrowserContext, mock: Mock, junk: Junk) -> Step:
        sent = len(mock.keys)
        watch = Watch(context)
        try:
            self.prepare(watch)
            filled = self.fill_junk(watch.page, junk)
            self.submit(watch.page)
            watch.page.wait_for_timeout(SETTLE_MS)
            message = self.message(watch.page)
            shots = [self.shot(watch.page, f"junk-{junk.name}")]
        except PlaywrightError as exc:
            return Step(
                done=junk.name,
                problems=[f"{junk.name}: {first_line(exc.message)}"],
                console=[f"{junk.name} {line}" for line in watch.lines],
            )
        finally:
            watch.page.close()
        return Step(
            done=(
                f"{junk.name}: {filled} fields, {len(mock.keys) - sent} requests,"
                f" shows {message!r}"
            ),
            problems=[f"{junk.name}: {p}" for p in watch.problems(mock.expected)],
            console=[f"{junk.name} {line}" for line in watch.lines],
            shots=shots,
        )

    def junk(self) -> Step:
        context = self.context()
        try:
            mock = Mock(context, self.form.post, self.accept)
            parts = [self.junk_one(context, mock, junk) for junk in JUNK]
            return Step(
                done="; ".join(part.done for part in parts),
                problems=[
                    *mock.problems(),
                    *(problem for part in parts for problem in part.problems),
                ],
                requests=len(mock.keys),
                keys=len(set(mock.keys)),
                console=[line for part in parts for line in part.console],
                shots=[shot for part in parts for shot in part.shots],
            )
        finally:
            context.close()

    def real(self) -> Step:
        context = self.context()
        try:
            watch = Watch(context, fake_sockets=False)
            if self.form.real == "spoof":
                return self.real_spoof(watch)
            return self.real_download(watch)
        finally:
            context.close()

    def real_download(self, watch: Watch) -> Step:
        page = watch.page
        page.goto(self.form.path, wait_until="load")
        cards = page.locator(f"{self.form.gallery} > .result-item")
        link = str(cards.evaluate_all(DOWNLOADED_LINK))
        if not link:
            return Step(
                problems=[
                    "no finished download with one link on the page to send again"
                ],
                console=watch.lines,
            )
        page.locator(DOWNLOAD_LINKS).fill(link)
        return self.real_submit(watch, f"sent {link} again", REAL_DOWNLOAD_MS)

    def real_spoof(self, watch: Watch) -> Step:
        page = watch.page
        page.goto(self.form.path, wait_until="load")
        cards = page.locator(f"{self.form.gallery} > .result-item[data-params]")
        source = str(cards.evaluate_all(SPOOF_SOURCE))
        if not source:
            return Step(
                problems=["no finished spoof on dev to take its video from"],
                console=watch.lines,
            )
        page.goto(f"{self.form.path}?video={quote(source, safe='')}", wait_until="load")
        if not within(page, ANSWER_MS, lambda: page.locator(SPOOF_PICKED).count() > 0):
            return Step(
                problems=[f"{source} did not show in the video picker"],
                console=watch.lines,
                shots=[self.shot(page, "real")],
            )
        return self.real_submit(watch, f"spoofed {Path(source).name}", REAL_SPOOF_MS)

    def real_submit(self, watch: Watch, what: str, wait_ms: int) -> Step:
        page = watch.page
        with page.expect_response(self.posted, timeout=REAL_ANSWER_MS) as answered:
            page.locator(self.form.submit).click()
        response = answered.value
        if response.status != ACCEPTED:
            return Step(
                problems=[f"the server answered {response.status}: {response.text()}"],
                requests=1,
                console=watch.lines,
                shots=[self.shot(page, "real")],
            )
        queued = Queued.model_validate(response.json())
        ids = queued.ids
        made = "a new job" if queued.job_ids else "no new job, the earlier one"
        if not ids:
            return Step(
                problems=["the server queued nothing and named no earlier job"],
                requests=1,
                console=watch.lines,
                shots=[self.shot(page, "real")],
            )
        cards = page.locator(f"{self.form.gallery} > .result-item")
        ready = within(page, wait_ms, lambda: bool(cards.evaluate_all(SETTLED, ids[0])))
        card = f'{self.form.gallery} > .result-item[data-job-id="{ids[0]}"]'
        failed = page.locator(f"{card} .result-failed").all_inner_texts()
        shots = [self.shot(page, "real")]
        problems = (
            []
            if ready
            else [f"no finished card of job {ids[0]} first after {wait_ms // 1000} s"]
        )
        problems += [f"the card failed: {text}" for text in failed]
        problems += watch.problems(never)
        return Step(
            done=f"{what}: {made} ({ids[0]}) finished, its card is first",
            problems=problems,
            requests=1,
            message=self.card(page),
            console=watch.lines,
            shots=shots,
        )


def guarded(name: str, run: Callable[[], Step]) -> Step:
    print(f"ui_check: {name}", flush=True)
    try:
        step = run()
    except Exception as exc:
        return Step(
            name=name,
            problems=[
                f"the step crashed: {type(exc).__name__}: {first_line(str(exc))}"
            ],
            console=traceback.format_exc().splitlines()[-12:],
        )
    return step.model_copy(update={"name": name})


def run_all(check: Check, real: bool) -> list[Step]:
    planned: dict[str, Callable[[], Step]] = {"load": check.load}
    if check.form.can_submit:
        planned |= {
            "answer 500": lambda: check.answered("500", CRASH.fulfill),
            "answer HTML": lambda: check.answered("html", HTML.fulfill),
            "offline": check.offline,
            "slow answer": check.slow,
            f"{CLICKS} clicks": check.clicks,
            "junk input": check.junk,
        }
    if real:
        planned["real submit"] = check.real
    return [guarded(name, run) for name, run in planned.items()]


def app_down(base_url: str) -> str | None:
    try:
        response = requests.get(f"{base_url}/health", timeout=HEALTH_S)
    except requests.RequestException as exc:
        return type(exc).__name__
    return None if response.ok else f"/health answered {response.status_code}"


class SignInError(Exception):
    pass


class DevUnreachableError(Exception):
    pass


def finish(word: Literal["PASS", "FAIL", "DOWN"], what: str) -> None:
    print(f"ui_check: {word} {what}", flush=True)


def signed_in(browser: Browser, base_url: str, password: str, folder: Path) -> Path:
    context = browser.new_context(base_url=base_url)
    state = folder / "state.json"
    try:
        sign_in(context.new_page(), password)
        context.storage_state(path=state)
    except SystemExit as exc:
        raise SignInError(str(exc).removeprefix("ui_screens: ")) from exc
    except PlaywrightError as exc:
        msg = f"could not sign in at {base_url}: {first_line(exc.message)}"
        raise DevUnreachableError(msg) from exc
    finally:
        context.close()
    return state


def make_media(folder: Path) -> Media:
    image = folder / "subject.png"
    Image.new("RGB", (256, 256), "crimson").save(image)
    video = folder / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=25",
            "-t",
            "3",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=60,
    )
    return Media(image=image, video=video)


def form_for(args: Args) -> PageForm:
    form = PAGES.get(args.path, PageForm(path=args.path))
    if args.submit and args.post:
        return form.model_copy(update={"submit": args.submit, "post": args.post})
    return form


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(prog="ui_check.py")
    parser.add_argument("path")
    parser.add_argument("--submit")
    parser.add_argument("--post")
    parser.add_argument("--real", action="store_true")
    return Args.model_validate(vars(parser.parse_args(argv)))


def refusal(args: Args, form: PageForm) -> str | None:
    if not args.path.startswith("/"):
        return f"the page is a path like /spoof, not {args.path!r}"
    if bool(args.submit) != bool(args.post):
        return "--submit and --post go together"
    if args.real and form.real is None:
        free = ", ".join(path for path, page in PAGES.items() if page.real)
        return f"--real only on {free}: {args.path} pays a provider, mock only"
    if not os.environ.get("OFM_UI_PASSWORD"):
        return "set OFM_UI_PASSWORD (APP_PASSWORD_ADMIN of .env)"
    return None


def checked(args: Args, form: PageForm, base_url: str, out: Path) -> list[Step]:
    with tempfile.TemporaryDirectory() as scratch, sync_playwright() as pw:
        browser = pw.chromium.launch()
        password = os.environ["OFM_UI_PASSWORD"]
        state = signed_in(browser, base_url, password, Path(scratch))
        out.mkdir(parents=True, exist_ok=True)
        media = make_media(Path(scratch)) if form.can_submit and form.picks else None
        steps = run_all(Check(browser, base_url, state, form, out, media), args.real)
        browser.close()
    return steps


def reported(args: Args, form: PageForm, base_url: str) -> Report:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = OUT / f"{args.path.strip('/').replace('/', '-') or 'root'}-{stamp}"
    steps = checked(args, form, base_url, out)
    report = Report(page=args.path, base_url=base_url, stamp=stamp, steps=steps)
    (out / "report.md").write_text(report.markdown(), encoding="utf-8")
    for step in report.steps:
        mark = "pass" if step.passed else "fail"
        print(f"ui_check: [{mark}] {step.name}: {step.detail}")
    finish("PASS" if report.passed else "FAIL", str(out / "report.md"))
    return report


def run(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
    except SystemExit:
        finish(
            "FAIL", "usage: ui_check.py <path> [--submit <sel> --post <glob>] [--real]"
        )
        return 2
    form = form_for(args)
    if refused := refusal(args, form):
        finish("FAIL", refused)
        return 2
    base_url = os.environ.get("OFM_UI_BASE_URL", "http://ofmhelpers:8000")
    if down := app_down(base_url):
        finish(
            "DOWN",
            f"the dev app at {base_url} does not answer ({down}); start it:"
            " docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d",
        )
        return 2
    try:
        report = reported(args, form, base_url)
    except DevUnreachableError as exc:
        finish("DOWN", str(exc))
        return 2
    except SignInError as exc:
        finish("FAIL", str(exc))
        return 2
    return 0 if report.passed else 1


def main() -> int:
    try:
        return run(sys.argv[1:])
    except Exception as exc:
        traceback.print_exc()
        finish(
            "FAIL", f"the check crashed: {type(exc).__name__}: {first_line(str(exc))}"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
