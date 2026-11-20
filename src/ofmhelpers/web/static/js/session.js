(() => {

    const REDIRECT_DELAY_MS = 1200;

    const IDLE_CHECK_MS = 30000;

    const KEEP_EVERY_MS = 60000;
    const KEEP_URL = "/session";

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

            return response
                .clone()
                .json()
                .then((body) => {
                    if (body?.login_url) {
                        expire(body.login_url);

                        return new Promise(() => {});
                    }
                    return response;
                })
                .catch(() => response);
        });

    function scheduleIdleExpiry() {
        const seconds = Number(document.body.dataset.sessionMaxAge || 0);
        if (!seconds) return;

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

    window.SessionExpiry = { expire };
})();
