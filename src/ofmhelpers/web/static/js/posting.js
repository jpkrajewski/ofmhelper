/*
 * "Start posting content" (base.html .posting-banner): a pop-up while the
 * daily posting window is open (web/posting.py), with a chime and a desktop
 * notification. Shown on load inside the window and at its start on a page
 * left open; hidden at its end. Dismissed (x), it stays away for that day's
 * window, on every page. The chime and the desktop notification come once
 * per window per browser (localStorage).
 *
 * Browsers allow sound and the notification permission prompt only after
 * the person clicks or types on the page: the first such gesture unlocks the
 * chime and asks for permission once; a window already open then chimes.
 * Times are Unix seconds (UTC).
 */
(() => {
    const banner = document.querySelector(".posting-banner");
    if (!banner) return;
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

    // --- chime (Web Audio: no sound file) ------------------------------------

    let audio = null;

    function unlockAudio() {
        if (audio) return audio;
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) return null;
        audio = new Context();
        return audio;
    }

    function chime() {
        if (!audio || audio.state !== "running") return false;
        const now = audio.currentTime;
        // Two rising tones, soft attack, short tail.
        [[880, 0], [1318.5, 0.18]].forEach(([freq, at]) => {
            const osc = audio.createOscillator();
            const gain = audio.createGain();
            osc.type = "sine";
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.0001, now + at);
            gain.gain.exponentialRampToValueAtTime(0.25, now + at + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.6);
            osc.connect(gain).connect(audio.destination);
            osc.start(now + at);
            osc.stop(now + at + 0.65);
        });
        return true;
    }

    // --- desktop notification ------------------------------------------------

    function notifyDesktop() {
        if (!("Notification" in window) || Notification.permission !== "granted") return;
        try {
            const note = new Notification(MESSAGE, { tag: `posting-${start}` });
            note.onclick = () => {
                window.focus();
                note.close();
            };
        } catch {
            // some mobile browsers only notify through a service worker
        }
    }

    // --- the window ------------------------------------------------------------

    function announce() {
        if (!remembered("posting-chimed") && chime()) remember("posting-chimed");
        if (!remembered("posting-notified")) {
            notifyDesktop();
            if ("Notification" in window && Notification.permission === "granted") {
                remember("posting-notified");
            }
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

    // The first click / key on any page: sound allowed from now on, and the
    // notification permission asked once.
    function onFirstGesture() {
        window.removeEventListener("pointerdown", onFirstGesture, true);
        window.removeEventListener("keydown", onFirstGesture, true);
        const ctx = unlockAudio();
        const resumed = ctx && ctx.state !== "running" ? ctx.resume() : Promise.resolve();
        if ("Notification" in window && Notification.permission === "default") {
            Notification.requestPermission().catch(() => {});
        }
        resumed.then(() => {
            if (open) announce();
        }).catch(() => {});
    }
    window.addEventListener("pointerdown", onFirstGesture, true);
    window.addEventListener("keydown", onFirstGesture, true);

    banner.querySelector(".posting-dismiss")?.addEventListener("click", () => {
        remember("posting-dismissed");
        banner.hidden = true;
    });
    tick();
})();
