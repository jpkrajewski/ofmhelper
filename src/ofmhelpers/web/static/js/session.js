/*
 * Session-expiry handling for every background request in the app.
 *
 * The problem this solves: the session cookie lasts 5 hours (see
 * SessionMiddleware in web/main.py). Leave a tab open longer than that and
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
 * else can fire a request.
 */
(function () {
    // How long the "Session expired" overlay is shown before the redirect --
    // long enough to read, short enough not to feel like a hang.
    const REDIRECT_DELAY_MS = 1200;

    // Wall-clock check interval for the idle-expiry deadline. Coarse on
    // purpose: it only needs to notice an expiry within ~30s of it happening.
    const IDLE_CHECK_MS = 30000;

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

    window.fetch = function (input, init) {
        return nativeFetch(input, init).then((response) => {
            if (response.status !== 401) return response;

            // Read the body from a clone -- the caller still needs an unread
            // stream, since a 401 that isn't a session expiry (a genuine
            // per-route 401) must reach its own error handling untouched.
            return response
                .clone()
                .json()
                .then((body) => {
                    if (body && body.login_url) {
                        expire(body.login_url);
                        // Never settle: the tab is navigating away, and
                        // resolving would let the caller paint an error card
                        // ("Lost track of this job") over the overlay first.
                        return new Promise(() => {});
                    }
                    return response;
                })
                .catch(() => response);
        });
    };

    // Expire the session on schedule too, not only when a request happens to
    // fail: an idle tab left open past the cookie's lifetime otherwise keeps
    // showing a logged-in UI until something is clicked. The deadline comes
    // from the server (base.html), so it stays in step with max_age.
    function scheduleIdleExpiry() {
        const seconds = Number(document.body.dataset.sessionMaxAge || 0);
        if (!seconds) return;

        // Wall-clock, not a plain setTimeout: a laptop asleep for 5h fires
        // timers late/not at all, and "the machine was suspended" is exactly
        // the case that produces a stale tab.
        const deadline = Date.now() + seconds * 1000;
        const loginUrl = "/login?next=" + encodeURIComponent(
            window.location.pathname + window.location.search
        );

        function tick() {
            if (Date.now() >= deadline) expire(loginUrl);
            else setTimeout(tick, IDLE_CHECK_MS);
        }
        setTimeout(tick, IDLE_CHECK_MS);
    }

    document.addEventListener("DOMContentLoaded", scheduleIdleExpiry);
})();
