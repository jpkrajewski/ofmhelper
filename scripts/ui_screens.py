"""Screenshots of every page of the running dev app, one folder per UI
revision, plus a side-by-side gallery of all revisions.

    C="docker compose -f docker-compose.yml -f docker-compose.dev.yml"
    MSYS_NO_PATHCONV=1 $C --profile test run --rm --no-deps \
        -e OFM_UI_PASSWORD="$(grep ^APP_PASSWORD_ADMIN= .env | cut -d= -f2-)" \
        tests uv run --frozen python scripts/ui_screens.py r01

Writes reports/ui/<revision>/<page>-<width>.png and reports/ui/index.html
(rows = pages, columns = revisions). Signs in as the admin password
account, so every admin page is shot too. Dev runs on prod data: the
shots stay in reports/ (gitignored), never in the repo.
"""

from __future__ import annotations

import os
import sys
from html import escape
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from pydantic import BaseModel

OUT = Path("reports/ui")
WIDTHS = (1440, 390)
# Pages that stream media never reach network idle: wait for load, then
# give the page's own scripts a moment to render.
SETTLE_MS = 1500


class Shot(BaseModel):
    name: str
    path: str
    signed_in: bool = True


PAGES = [
    Shot(name="landing", path="/", signed_in=False),
    Shot(name="login", path="/login", signed_in=False),
    Shot(name="home", path="/home"),
    Shot(name="generate", path="/generate"),
    Shot(name="clone", path="/clone"),
    Shot(name="clone-long", path="/clone-long"),
    Shot(name="clone-links", path="/clone-links"),
    Shot(name="reels", path="/reels"),
    Shot(name="download", path="/download-assets"),
    Shot(name="accounts", path="/accounts"),
    Shot(name="links", path="/links"),
    Shot(name="phones", path="/phones"),
    Shot(name="drive", path="/drive"),
    Shot(name="uniqify", path="/uniqify"),
    Shot(name="account", path="/account"),
    Shot(name="cookies", path="/cookies"),
    Shot(name="users", path="/users"),
    Shot(name="character-sheet", path="/character-sheet"),
    Shot(name="applications", path="/applications"),
    Shot(name="file-manager", path="/file-manager"),
    Shot(name="action-log", path="/action-log"),
    Shot(name="styleguide", path="/styleguide"),
]


def sign_in(page: Page, password: str) -> None:
    page.goto("/login")
    page.fill("#email", "admin")
    page.fill("#password", password)
    with page.expect_navigation():
        page.click("button[type=submit]")
    if "/login" in page.url:
        msg = "ui_screens: login failed -- check OFM_UI_PASSWORD"
        raise SystemExit(msg)


def first_account(page: Page) -> str | None:
    """/accounts/{id} of the first tracked account, if there is one."""
    page.goto("/accounts", wait_until="load")
    link = page.locator('a[href^="/accounts/"]').first
    return link.get_attribute("href") if link.count() else None


def shoot(page: Page, shot: Shot, revision: Path) -> None:
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(shot.path, wait_until="load")
        # Lazy images load only once scrolled near: walk down, then back.
        page.evaluate(
            "async () => { for (let y = 0; y < document.body.scrollHeight; y += 600)"
            " { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 60)); }"
            " window.scrollTo(0, 0); }"
        )
        page.wait_for_timeout(SETTLE_MS)
        broken = page.evaluate(
            "() => [...document.images].filter(i => i.complete && i.naturalWidth === 0"
            " && i.currentSrc).map(i => i.currentSrc)"
        )
        if broken:
            print(
                f"ui_screens: {shot.name}-{width}: {len(broken)} broken images, e.g. {broken[0]}"
            )
        page.screenshot(path=revision / f"{shot.name}-{width}.png", full_page=True)


def gallery() -> None:
    """index.html: one row per page and width, one column per revision."""
    revisions = sorted(p.name for p in OUT.iterdir() if p.is_dir())
    names = sorted({p.stem for r in revisions for p in (OUT / r).glob("*.png")})
    head = "".join(f"<th>{escape(r)}</th>" for r in revisions)
    rows = []
    for name in names:
        cells = []
        for r in revisions:
            img = OUT / r / f"{name}.png"
            src = f"{escape(r)}/{escape(name)}.png"
            cells.append(
                f'<td><a href="{src}"><img src="{src}" loading="lazy"></a></td>'
                if img.exists()
                else "<td>-</td>"
            )
        rows.append(f"<tr><th>{escape(name)}</th>{''.join(cells)}</tr>")
    (OUT / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>UI revisions</title>"
        "<style>body{font:14px system-ui;margin:16px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ddd;padding:6px;vertical-align:top}"
        "th{position:sticky;top:0;background:#fff;text-align:left}"
        "img{width:420px;display:block}</style>"
        f"<table><tr><th>page</th>{head}</tr>{''.join(rows)}</table>"
    )


def main() -> None:
    args = sys.argv[1:]
    if len(args) != 1:
        msg = "usage: ui_screens.py <revision label, e.g. r01>"
        raise SystemExit(msg)
    revision = OUT / args[0]
    revision.mkdir(parents=True, exist_ok=True)
    base_url = os.environ.get("OFM_UI_BASE_URL", "http://ofmhelpers:8000")
    password = os.environ["OFM_UI_PASSWORD"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        public = browser.new_page(base_url=base_url)
        for shot in (s for s in PAGES if not s.signed_in):
            shoot(public, shot, revision)
        page = browser.new_page(base_url=base_url)
        sign_in(page, password)
        shots = [s for s in PAGES if s.signed_in]
        if detail := first_account(page):
            shots.append(Shot(name="account-analytics", path=detail))
        for shot in shots:
            shoot(page, shot, revision)
        browser.close()
    gallery()
    print(f"ui_screens: {revision} ({len(list(revision.glob('*.png')))} shots)")


if __name__ == "__main__":
    main()
