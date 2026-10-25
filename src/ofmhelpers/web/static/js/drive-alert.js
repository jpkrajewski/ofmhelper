/*
 * "Google Drive is full" (base.html .drive-full-banner, web/drive_space.py):
 * every signed-in page asks /account/drive-space in the background (cached
 * on the server: no page waits on Google) and, while the person's Drive is
 * under the floor, shows the red pop-up with a chime and a desktop
 * notification (notify.js) once a day per browser. Dismissed (x), it stays
 * away for the rest of that day, on every page.
 */
(() => {
    const banner = document.querySelector(".drive-full-banner");
    if (!banner || !window.Notifier) return;
    const today = new Date().toISOString().slice(0, 10);
    const MESSAGE = "Google Drive is full. Check the folder.";

    function remembered(key) {
        try {
            return window.localStorage.getItem(key) === today;
        } catch {
            return false;
        }
    }

    function remember(key) {
        try {
            window.localStorage.setItem(key, today);
        } catch {
            // private window: only for this page
        }
    }

    function announce() {
        if (banner.hidden) return;
        if (!remembered("drive-full-chimed") && window.Notifier.chime()) remember("drive-full-chimed");
        if (!remembered("drive-full-notified") && window.Notifier.desktop(MESSAGE, `drive-full-${today}`)) {
            remember("drive-full-notified");
        }
    }

    window.Notifier.onUnlock(announce);
    banner.querySelector(".drive-full-dismiss")?.addEventListener("click", () => {
        remember("drive-full-dismissed");
        banner.hidden = true;
    });

    if (remembered("drive-full-dismissed")) return;
    fetch("/account/drive-space")
        .then((r) => (r.ok ? r.json() : null))
        .then((space) => {
            if (!space || !space.low) return;
            banner.querySelector(".drive-full-text").textContent =
                `Google Drive is full (${space.free_text} left). Check the folder.`;
            banner.querySelector(".drive-full-folder").href = space.folder_url;
            banner.querySelector(".drive-full-buy").href = space.buy_url;
            banner.hidden = false;
            announce();
        })
        .catch(() => {});
})();
