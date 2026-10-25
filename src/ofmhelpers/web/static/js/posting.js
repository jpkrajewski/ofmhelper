/*
 * "Start posting content" (base.html .posting-banner): a pop-up while the
 * daily posting window is open (web/posting.py), with a chime and a desktop
 * notification (notify.js). Shown on load inside the window and at its start
 * on a page left open; hidden at its end. Dismissed (x), it stays away for
 * that day's window, on every page. The chime and the desktop notification
 * come once per window per browser (localStorage); a window already open
 * when the first click unlocks sound then chimes.
 * Times are Unix seconds (UTC).
 */
(() => {
    const banner = document.querySelector(".posting-banner");
    if (!banner || !window.Notifier) return;
    const DAY = 24 * 60 * 60;
    const MESSAGE = "Start posting content";
    let start = Number(banner.dataset.start);
    let end = Number(banner.dataset.end);
    if (!start || !end) return;

    function remembered(key) {
        try {
            return window.localStorage.getItem(key) === String(start);
        } catch {
            return false;
        }
    }

    function remember(key) {
        try {
            window.localStorage.setItem(key, String(start));
        } catch {
            // private window: only for this page
        }
    }

    function announce() {
        if (!remembered("posting-chimed") && window.Notifier.chime()) remember("posting-chimed");
        if (!remembered("posting-notified") && window.Notifier.desktop(MESSAGE, `posting-${start}`)) {
            remember("posting-notified");
        }
    }

    let open = false;

    function tick() {
        const now = Date.now() / 1000;
        while (now >= end) {
            start += DAY;
            end += DAY;
        }
        open = now >= start && now < end;
        banner.hidden = !open || remembered("posting-dismissed");
        if (open) announce();
        const next = (open ? end : start) - now;
        // Timers drift while a laptop sleeps: re-check at least every minute.
        setTimeout(tick, Math.min(Math.max(next, 1), 60) * 1000);
    }

    window.Notifier.onUnlock(() => {
        if (open) announce();
    });
    banner.querySelector(".posting-dismiss")?.addEventListener("click", () => {
        remember("posting-dismissed");
        banner.hidden = true;
    });
    tick();
})();
