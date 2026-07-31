"""
Free, no-login Instagram scraping via Playwright (headless Chromium) --
no Apify actor, no credits spent, no account login. Reads only what a
logged-out visitor to instagram.com sees, so it's inherently limited --
but every field below was confirmed against a live public profile, not
guessed:

- followers: `og:description` meta tag on the profile page reads
  "12.3K Followers, ...".
- views: the `/<username>/reels/` grid (NOT the plain profile grid, which
  shows no counts at all) renders each thumbnail with a "View Count Icon"
  SVG immediately followed by the view count as plain text, in the same
  order as the reel links -- confirmed live (e.g. "View Count Icon10.2K"
  next to a reel's `<a href>`). This is the one place Instagram exposes
  view counts to a logged-out visitor.
- likes / comments: a logged-out reel *detail* page renders exactly two
  bare (no "likes"/"comments" label) numeric leaf elements, in order:
  like count then comment count. `None` if the layout doesn't match
  (private account, markup drift, zero engagement so the element is
  omitted).
- banned/deleted/renamed accounts: served as a normal 200 page reading
  "Sorry, this page isn't available." (or, depending on the browser's
  locale, "Przepraszamy, ta strona jest niedostępna") -- detected
  explicitly (`_check_available`), otherwise it scrapes as a live account
  with zero of everything.
- shares: Instagram has never exposed a share count anywhere a logged-out
  (or even logged-in, non-owner) visitor can see it -- only the account
  owner's own Insights tab shows it. There is no free, in-house way to get
  a real number without owning the account's login, so this scraper does
  not report shares at all.

An anonymous instaloader (the other well-known free IG scraper) attempt
against the same account hit an immediate 429 from Instagram's private
API -- the account-less API is rate-limited far harder than the plain
HTML the profile/reels-tab pages serve, so this module deliberately
stays on Playwright + public HTML rather than that private endpoint.

If a cookies.txt is uploaded via /cookies (downloaders.cookies -- normally
used for yt-dlp's authenticated downloads), its instagram.com cookies are
loaded into the browser context too. Logged-in requests get Instagram's
soft anonymous-scraper block far less than logged-out ones; this doesn't
unlock anything logged-out couldn't eventually see (shares are still
Insights-only, see above), it just makes the same public data more
reliable to fetch.

Fault-isolated by design: one account's scrape failing (rate limit,
markup change, private account) must not take down the nightly sweep over
every other account -- see instagram_stats_job.py, which calls this per
account and logs+continues on any exception.

`fetch_profile_stats` (the public entry point) always runs the actual
Playwright work in a *subprocess* (`python -m ofmhelpers.scraping.
instagram_public`), never in-process. Reason: this job runs on the RQ
worker (worker.py), and RQ forks a fresh OS process per job
(`Worker.execute_job` -> `fork_work_horse`) rather than `exec`-ing one.
Playwright's sync API keeps its own background thread + event loop; a
`fork()` that happens after that thread exists (or after any other thread
in the process has touched a lock) can leave the child permanently
deadlocked before it ever spawns a browser -- confirmed live: the exact
same scrape completed in ~25s under `docker compose exec` (an exec, not a
fork) and hung indefinitely as an RQ job on the same image. Routing
through a real subprocess sidesteps fork+thread entirely; the small
process-startup overhead is irrelevant for a once-a-day sweep over a
handful of accounts.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unicodedata

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

PROFILE_URL = "https://www.instagram.com/{username}/"
REELS_URL = "https://www.instagram.com/{username}/reels/"

_FOLLOWERS_RE = re.compile(r"([\d,.]+)\s*([kKmM]?)\s*Followers", re.IGNORECASE)
_VIEW_COUNT_RE = re.compile(r"View Count Icon\s*([\d,.]+)\s*([kKmM]?)", re.IGNORECASE)
_BARE_DIGITS_JS = """
els => els
    .filter(e => e.childElementCount === 0)
    .map(e => e.textContent.trim())
    .filter(t => /^\\d{1,8}$/.test(t))
"""
_BLOCKED_TITLE_MARKERS = ("page couldn't load", "page not found")
# Instagram serves a banned/deleted/renamed account the same "sorry, this
# page isn't available" body (HTTP 200) it serves a typo'd username, and it
# is localised -- the container's locale decides which language comes back,
# so both the English and the Polish wording have to be recognised. Matched
# on a short distinctive fragment rather than the full sentence: the trailing
# copy ("the link you followed may be broken...") gets reworded far more
# often than the headline does.
_PUNCTUATION_RE = re.compile(r"[^a-z0-9\s]+")
_UNAVAILABLE_MARKERS = (
    "page isnt available",  # EN (punctuation-stripped, see _normalize_text)
    "strona jest niedostepna",  # PL
)
UNAVAILABLE_ERROR = "account unavailable (banned, deleted or renamed)"
_NETSCAPE_COOKIE_FIELDS = 7
_REEL_GRID_JS = """
els => els.map(e => {
    const a = e.closest("a");
    const container = e.closest("li") || e.parentElement.parentElement.parentElement;
    return {href: a ? a.getAttribute("href") : null, text: container ? container.textContent : ""};
})
"""


def parse_count(raw: str, suffix: str) -> int:
    """ "1,234" -> 1234, "12.3" + "K" -> 12300, "1.2" + "M" -> 1200000."""
    value = float(raw.replace(",", ""))
    multiplier = {"k": 1_000, "m": 1_000_000}.get(suffix.lower(), 1)
    return int(value * multiplier)


def _extract_count(pattern: re.Pattern, text: str) -> int | None:
    match = pattern.search(text)
    if not match:
        return None
    return parse_count(match.group(1), match.group(2))


class PostStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    views: int | None
    likes: int | None
    comments: int | None


class ProfileStats(BaseModel):
    """Crosses a process boundary: the scrape runs in a subprocess (see the
    module docstring) and this is what it writes to stdout, so the model owns
    both directions of that hop rather than a pair of hand-rolled dict walks."""

    model_config = ConfigDict(extra="forbid")

    username: str
    followers: int | None
    posts: list[PostStats]
    error: str | None = None


def _load_instagram_cookies() -> list[dict]:
    """Parses the uploaded Netscape-format cookies.txt (see
    downloaders/cookies.py) into Playwright's cookie dict shape, keeping
    only instagram.com cookies. Returns [] if no file was uploaded --
    callers then just scrape logged-out, exactly as before."""
    from pathlib import Path

    from ofmhelpers.downloaders.cookies import get_cookiefile

    path = get_cookiefile()
    if path is None:
        return []

    cookies = []
    with Path(path).open(encoding="utf-8") as f:
        for raw_line in f:
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split("\t")
            if len(fields) != _NETSCAPE_COOKIE_FIELDS:
                continue
            domain, _flag, cpath, secure, expires, name, value = fields
            if "instagram.com" not in domain:
                continue
            cookies.append(
                {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": cpath,
                    "expires": float(expires) if expires != "0" else -1,
                    "secure": secure == "TRUE",
                }
            )
    return cookies


def _launch_page(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 900},
    )
    cookies = _load_instagram_cookies()
    if cookies:
        context.add_cookies(cookies)
    return browser, context.new_page()


def _check_not_blocked(page) -> None:
    """Instagram answers an over-eager anonymous scraper with a rendered
    "Page couldn't load" page (HTTP 200, not an exception) rather than an
    error status -- indistinguishable from "this reel has zero engagement"
    unless checked explicitly. Raises so the caller records it as an error
    instead of silently saving an empty/zero result."""
    title = (page.title() or "").lower()
    if any(marker in title for marker in _BLOCKED_TITLE_MARKERS):
        msg = f"Instagram blocked the request (page title: {page.title()!r})"
        raise RuntimeError(msg)


def _normalize_text(text: str) -> str:
    """Lowercase, ASCII-fold and drop punctuation, so one plain marker matches
    whichever apostrophe Instagram rendered ("isn't" typographic or straight)
    and Polish diacritics ("niedostepna") alike."""
    folded = unicodedata.normalize("NFKD", text.lower())
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    return _PUNCTUATION_RE.sub("", ascii_only)


def is_unavailable_page(text: str) -> bool:
    return any(marker in _normalize_text(text) for marker in _UNAVAILABLE_MARKERS)


def _check_available(page) -> None:
    """A banned, deleted or renamed account is not an HTTP error: Instagram
    answers 200 with "Sorry, this page isn't available." / "Przepraszamy, ta
    strona jest niedostępna". Left unchecked that scrapes as zero followers
    and no posts -- indistinguishable from a quiet account. Raises so the
    sweep records why the account went dark instead."""
    try:
        body = page.inner_text("body")
    except Exception:
        return
    if is_unavailable_page(body):
        raise RuntimeError(UNAVAILABLE_ERROR)


def _meta_description(page) -> str:
    el = page.locator('meta[property="og:description"]').first
    return el.get_attribute("content") or "" if el.count() else ""


def _reel_grid(page, limit: int) -> list[tuple[str, int | None]]:
    """[(post_url, views), ...] in grid order (newest first), from the
    `/<username>/reels/` tab -- the one page that shows view counts."""
    rows = page.eval_on_selector_all('svg[aria-label="View Count Icon"]', _REEL_GRID_JS)
    out: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    for row in rows:
        href = row.get("href")
        if not href or href in seen:
            continue
        seen.add(href)
        views = _extract_count(_VIEW_COUNT_RE, row.get("text") or "")
        out.append((f"https://www.instagram.com{href}", views))
        if len(out) >= limit:
            break
    return out


def _likes_and_comments(page) -> tuple[int | None, int | None]:
    """The first two bare numeric leaf elements on a reel detail page are
    the like count then the comment count -- see module docstring."""
    digits = page.eval_on_selector_all("span, div", _BARE_DIGITS_JS)
    likes = int(digits[0]) if len(digits) > 0 else None
    comments = int(digits[1]) if len(digits) > 1 else None
    return likes, comments


def _fetch_profile_stats_in_process(username: str, last_n_posts: int) -> ProfileStats:
    """The actual Playwright scrape. Only safe to call from a freshly
    `exec`-ed process (a plain script run, a `docker compose exec`, this
    module's own `__main__` block) -- never from a forked child, see the
    module docstring. Use `fetch_profile_stats` instead of this directly."""
    from playwright.sync_api import sync_playwright  # heavy import, deferred

    cfg = settings.instagram_stats
    try:
        with sync_playwright() as pw:
            browser, page = _launch_page(pw)
            try:
                page.goto(
                    PROFILE_URL.format(username=username),
                    wait_until="domcontentloaded",
                    timeout=cfg.nav_timeout_ms,
                )
                # let the profile header client-render
                page.wait_for_timeout(cfg.render_wait_ms)
                _check_not_blocked(page)
                _check_available(page)
                followers = _extract_count(_FOLLOWERS_RE, _meta_description(page))

                page.goto(
                    REELS_URL.format(username=username),
                    wait_until="domcontentloaded",
                    timeout=cfg.nav_timeout_ms,
                )
                # let the reels grid client-render
                page.wait_for_timeout(cfg.render_wait_ms)
                _check_not_blocked(page)
                reel_grid = _reel_grid(page, last_n_posts)
                if not reel_grid:
                    # Grid hydration is timing-sensitive (cookie-consent banner,
                    # first-load jank) -- one longer-wait retry before giving up.
                    page.wait_for_timeout(cfg.grid_retry_wait_ms)
                    _check_not_blocked(page)
                    reel_grid = _reel_grid(page, last_n_posts)

                posts: list[PostStats] = []
                for post_url, views in reel_grid:
                    try:
                        page.goto(
                            post_url,
                            wait_until="domcontentloaded",
                            timeout=cfg.nav_timeout_ms,
                        )
                        page.wait_for_timeout(cfg.reel_render_wait_ms)
                        likes, comments = _likes_and_comments(page)
                        posts.append(
                            PostStats(
                                url=post_url,
                                views=views,
                                likes=likes,
                                comments=comments,
                            )
                        )
                    except Exception:
                        logger.warning(
                            "instagram post scrape failed url=%s",
                            post_url,
                            exc_info=True,
                        )
                        posts.append(
                            PostStats(
                                url=post_url, views=views, likes=None, comments=None
                            )
                        )

                return ProfileStats(username=username, followers=followers, posts=posts)
            finally:
                browser.close()
    except Exception as exc:
        logger.warning(
            "instagram profile scrape failed username=%s", username, exc_info=True
        )
        return ProfileStats(username=username, followers=None, posts=[], error=str(exc))


def _run_and_parse_subprocess(username: str, last_n_posts: int) -> ProfileStats:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ofmhelpers.scraping.instagram_public",
            username,
            str(last_n_posts),
        ],
        capture_output=True,
        text=True,
        timeout=settings.instagram_stats.subprocess_timeout_s,
        check=True,
    )
    return ProfileStats.model_validate_json(result.stdout)


def fetch_profile_stats(username: str, last_n_posts: int | None = None) -> ProfileStats:
    """Public entry point: runs the actual scrape in a subprocess (see the
    module docstring for why -- fork+Playwright deadlocks on the RQ
    worker). Never raises; a subprocess crash/timeout/bad-JSON becomes a
    ProfileStats with `error` set, exactly like an in-process failure."""
    if last_n_posts is None:
        last_n_posts = settings.instagram_stats.last_n_posts
    try:
        return _run_and_parse_subprocess(username, last_n_posts)
    except subprocess.CalledProcessError as exc:
        # The stderr tail is the only place the child's traceback survives, so
        # it is logged as its own argument rather than folded into the message.
        logger.warning(
            "instagram stats subprocess failed username=%s stderr=%s",
            username,
            exc.stderr[-500:],
            exc_info=True,
        )
        msg = f"scrape subprocess exited {exc.returncode}: {exc.stderr[-500:]}"
        return ProfileStats(username=username, followers=None, posts=[], error=msg)
    except Exception as exc:
        logger.warning(
            "instagram stats subprocess failed username=%s", username, exc_info=True
        )
        return ProfileStats(username=username, followers=None, posts=[], error=str(exc))


_CLI_MIN_ARGC = 2

if __name__ == "__main__":
    _username = sys.argv[1]
    _last_n = (
        int(sys.argv[2])
        if len(sys.argv) > _CLI_MIN_ARGC
        else settings.instagram_stats.last_n_posts
    )
    _stats = _fetch_profile_stats_in_process(_username, _last_n)
    sys.stdout.write(_stats.model_dump_json())
