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
