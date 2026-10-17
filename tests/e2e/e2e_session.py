"""A logged-in browser tab for the e2e suite (imported by its conftest and
tests; pytest puts this directory on sys.path, there is no tests package)."""

import os

from playwright.sync_api import Browser

BASE_URL = os.environ.get("OFM_E2E_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


class Session:
    """One logged-in tab, collecting every JS error the pages raise."""

    def __init__(self, browser: Browser, password: str) -> None:
        self.page = browser.new_context(accept_downloads=True).new_page()
        self.js_errors: list[str] = []
        self.page.on("pageerror", lambda exc: self.js_errors.append(str(exc)))
        self.page.on(
            "console",
            lambda msg: msg.type == "error" and self.js_errors.append(msg.text),
        )
        self.page.goto(f"{BASE_URL}/login")
        self.page.fill("#password", password)
        # Wait for the redirect, not for network idle: the landing page it
        # lands on keeps streaming media, which never goes idle.
        with self.page.expect_navigation():
            self.page.click("button[type=submit]")
        assert "/login" not in self.page.url, "login failed -- wrong password?"

    def goto(self, path: str) -> int:
        response = self.page.goto(f"{BASE_URL}{path}")
        assert response is not None
        return response.status
