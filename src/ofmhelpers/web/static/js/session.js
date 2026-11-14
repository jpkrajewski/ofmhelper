/*
 * Session-expiry handling for every background request in the app.
 *
 * The problem this solves: the session cookie lasts 5 hours after the
 * person's last action (middleware/auth.py slides it on every page opened
 * and every write). Leave a tab untouched longer than that and
 * the page is still fully interactive -- nav links, buttons, forms -- but
 * every fetch() behind them is unauthenticated. AuthMiddleware now answers
 * those with 401 + {"login_url": ...} instead of a 303 to the login page
 * (which fetch follows transparently, handing the JS login *HTML* with
 * status 200 -- an opaque JSON parse error at best, login markup injected
 * into a table cell at worst).
 *
 * Rather than teach every call site about 401s, fetch is wrapped once here:
 * any 401 carrying a login_url takes over the whole tab. Loaded before every
 * other script in base.html, so the wrap is in place by the time anything
 * else can fire a request. An XMLHttpRequest (an upload with progress,
 * generation.js) is not wrapped: it calls `SessionExpiry.expire` itself.
 */
(() => {
    // How long the "Session expired" overlay is shown before the redirect --
    // long enough to read, short enough not to feel like a hang.
    const REDIRECT_DELAY_MS = 1200;

    // Wall-clock check interval for the idle-expiry deadline. Coarse on
    // purpose: it only needs to notice an expiry within ~30s of it happening.
    const IDLE_CHECK_MS = 30000;

    // A click or key press tells the server the person is still here, at
    // most this often: the answer slides the cookie, and the deadline below
    // with it.
    const KEEP_EVERY_MS = 60000;
    const KEEP_URL = "/session";

    // Multiple in-flight requests can 401 together (the poller plus a
    // submit). The first one wins; the rest are swallowed so we don't stack
    // redirects or flash several overlays.
    let expiring = false;

    function overlay() {
        const el = document.createElement("div");
        el.className = "session-expired-overlay";
        el.innerHTML =
            '<div class="session-expired-card">' +
            '<div class="session-expired-title">Session expired</div>' +
            "<p>You've been signed out. Taking you back to the login page…</p>" +
            "</div>";
        return el;
    }

    // A hard redirect rather than a silent re-login: the page's state (a
    // half-filled form, a stale gallery) belongs to a session that no longer
    // exists, so continuing in place would be lying about what's saved.
    // The brief overlay exists so the navigation reads as a deliberate
    // "you were logged out" rather than a random page jump.
    function expire(loginUrl) {
        if (expiring) return;
        expiring = true;
        document.body.appendChild(overlay());
        setTimeout(() => {
            window.location.href = loginUrl;
        }, REDIRECT_DELAY_MS);
    }

    const nativeFetch = window.fetch.bind(window);

    window.fetch = (input, init) => nativeFetch(input, init).then((response) => {
            if (response.status !== 401) return response;

            // Read the body from a clone -- the caller still needs an unread
            // stream, since a 401 that isn't a session expiry (a genuine
            // per-route 401) must reach its own error handling untouched.
            return response
                .clone()
                .json()
                .then((body) => {
                    if (body?.login_url) {
                        expire(body.login_url);
                        // Never settle: the tab is navigating away, and
                        // resolving would let the caller paint an error card
                        // over the overlay first.
                        return new Promise(() => {});
                    }
                    return response;
                })
                .catch(() => response);
        });

    // Expire the session on schedule too, not only when a request happens to
    // fail: an idle tab left open past the cookie's lifetime otherwise keeps
    // showing a logged-in UI until something is clicked. The deadline comes
    // from the server (base.html), so it stays in step with max_age, and
    // moves each time the server slides the cookie (`keepOnActivity`).
    function scheduleIdleExpiry() {
        const seconds = Number(document.body.dataset.sessionMaxAge || 0);
        if (!seconds) return;

        // Wall-clock, not a plain setTimeout: a laptop asleep for 5h fires
        // timers late/not at all, and "the machine was suspended" is exactly
        // the case that produces a stale tab.
        let deadline = Date.now() + seconds * 1000;
        keepOnActivity(() => {
            deadline = Date.now() + seconds * 1000;
        });
        const loginUrl = `/login?next=${encodeURIComponent(
            window.location.pathname + window.location.search
        )}`;

        function tick() {
            if (Date.now() >= deadline) expire(loginUrl);
            else setTimeout(tick, IDLE_CHECK_MS);
        }
        setTimeout(tick, IDLE_CHECK_MS);
    }

    // The person working on one page for hours (typing a prompt, watching
    // cards) makes no request the server counts as theirs: polls are not.
    // Their clicks and key presses are, through one small PUT. A 401 here
    // takes the tab like any other (the fetch wrap above).
    function keepOnActivity(slid) {
        let last = Date.now();
        function keep() {
            if (expiring || Date.now() - last < KEEP_EVERY_MS) return;
            last = Date.now();
            window
                .fetch(KEEP_URL, { method: "PUT" })
                .then((response) => {
                    if (response.ok) slid();
                })
                .catch(() => {});
        }
        document.addEventListener("pointerdown", keep, { passive: true });
        document.addEventListener("keydown", keep, { passive: true });
    }

    document.addEventListener("DOMContentLoaded", scheduleIdleExpiry);

    // For requests fetch cannot make: an upload with progress is an
    // XMLHttpRequest (generation.js), whose 401 takes the tab the same way.
    window.SessionExpiry = { expire };
})();
