/*
 * Google Drive pop-ups (base.html, web/drive_space.py): every signed-in page
 * asks /account/drive-space in the background (cached on the server: no
 * page waits on Google) and shows the one that applies -- "Google Drive is
 * full" (.drive-full-banner) while the person's Drive is under the floor,
 * "Google Drive disconnected" (.drive-reconnect-banner) while Google refuses
 * their sign-in -- with a chime and a desktop notification (notify.js) once
 * a day per browser. Dismissed (x), it stays away for the rest of that day,
 * on every page.
 */
(() => {
    if (!window.Notifier) return;
    const today = new Date().toISOString().slice(0, 10);
    const full = popup(".drive-full-banner", "drive-full", "Google Drive is full. Check the folder.");
    const reconnect = popup(".drive-reconnect-banner", "drive-reconnect", "Google Drive disconnected. Reconnect it.");
    if (!full || !reconnect) return;

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

    function popup(selector, key, message) {
        const banner = document.querySelector(selector);
        return banner ? { banner, key, message } : null;
    }

    function announce(one) {
        if (one.banner.hidden) return;
        if (!remembered(`${one.key}-chimed`) && window.Notifier.chime()) remember(`${one.key}-chimed`);
        if (!remembered(`${one.key}-notified`) && window.Notifier.desktop(one.message, `${one.key}-${today}`)) {
            remember(`${one.key}-notified`);
        }
    }

    function show(one) {
        if (remembered(`${one.key}-dismissed`)) return;
        one.banner.hidden = false;
        announce(one);
    }

    for (const one of [full, reconnect]) {
        window.Notifier.onUnlock(() => announce(one));
        one.banner.querySelector(`.${one.key}-dismiss`)?.addEventListener("click", () => {
            remember(`${one.key}-dismissed`);
            one.banner.hidden = true;
        });
    }
    // Straight to Google's consent, then back to this page (the markup's
    // /account is the no-script way there).
    const here = window.location.pathname + window.location.search;
    reconnect.banner.querySelector(".drive-reconnect-link").href =
        `/auth/google/start?link=1&next=${encodeURIComponent(here)}`;

    if (remembered("drive-full-dismissed") && remembered("drive-reconnect-dismissed")) return;
    fetch("/account/drive-space")
        .then((r) => (r.ok ? r.json() : null))
        .then((space) => {
            if (!space) return;
            if (space.reconnect) {
                show(reconnect);
                return;
            }
            if (!space.low) return;
            full.banner.querySelector(".drive-full-text").textContent =
                `Google Drive is full (${space.free_text} left). Check the folder.`;
            full.banner.querySelector(".drive-full-folder").href = space.folder_url;
            full.banner.querySelector(".drive-full-buy").href = space.buy_url;
            show(full);
        })
        .catch(() => {});
})();
