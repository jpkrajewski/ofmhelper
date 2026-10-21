"""A logged-in browser tab for the e2e suite (imported by its conftest and
tests; pytest puts this directory on sys.path, there is no tests package)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ofmhelpers.instagram.fake import cdn_reply
from ofmhelpers.instagram.models import GLOBAL_CDN_HOST

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Browser, Response, Route

# The app's Drive root under OFM_FAKE_EXTERNALS (conftest `_app_env`).
FAKE_DRIVE_ROOT = "fake-root-folder-e2e"


def log_in(browser: Browser, base_url: str, password: str, state: Path) -> None:
    """Logs in once and saves the cookies to `state`: every test of that
    role starts from them instead of the login form."""
    context = browser.new_context(base_url=base_url)
    page = context.new_page()
    page.goto("/login")
    page.fill("#password", password)
    # Wait for the redirect, not for network idle: the landing page it
    # lands on keeps streaming media, which never goes idle.
    with page.expect_navigation():
        page.click("button[type=submit]")
    assert "/login" not in page.url, "login failed -- wrong password?"
    context.storage_state(path=state)
    context.close()


_CDN = re.compile(rf"^https://{re.escape(GLOBAL_CDN_HOST)}/")


def _serve_cdn(route: Route) -> None:
    reply = cdn_reply(route.request.url)
    route.fulfill(
        status=reply.status,
        headers={**reply.headers, "Access-Control-Allow-Origin": "*"},
        body=reply.body,
    )


class Session:
    """One logged-in tab (its own browser context), recording every JS
    error and failed request, traced so a failure can be replayed."""

    def __init__(self, browser: Browser, base_url: str, state: Path) -> None:
        self.context = browser.new_context(
            base_url=base_url, storage_state=state, accept_downloads=True
        )
        self.context.tracing.start(screenshots=True, snapshots=True)
        # Pages load reel videos and thumbnails straight from Instagram's
        # CDN: the browser gets them from the same fake as the app.
        self.context.route(_CDN, _serve_cdn)
        self.page = self.context.new_page()
        self.js_errors: list[str] = []
        self.failed_requests: list[str] = []
        self.page.on("pageerror", lambda exc: self.js_errors.append(str(exc)))
        self.page.on(
            "console",
            lambda msg: msg.type == "error" and self.js_errors.append(msg.text),
        )
        self.page.on(
            "requestfailed",
            lambda r: self.failed_requests.append(
                f"{r.method} {r.url}: {r.failure or 'failed'}"
            ),
        )
        self.page.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        if response.status >= 400:
            self.failed_requests.append(
                f"{response.request.method} {response.url}: {response.status}"
            )

    def goto(self, path: str) -> int:
        response = self.page.goto(path)
        assert response is not None
        return response.status

    def close(self, trace: Path | None) -> None:
        """Ends the tab; `trace` keeps the Playwright trace there."""
        self.context.tracing.stop(path=trace)
        self.context.close()
