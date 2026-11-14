/*
 * The header's clock (`ui.clock_now`) and the zone every page's times are in
 * (schemas/time_zones.py, cookie `ofm_timezone`).
 *
 * The zone starts as the person's own: the browser knows the zone of the
 * computer or phone they sit at, and until they pick another the cookie
 * follows it (also when they travel). The globe's menu (`ui.time_zone_menu`)
 * picks one of the team's zones, remembered in this browser; "Automatic"
 * goes back to their own. A page printing times in the zone
 * (`data-zone-times`) reloads to follow; any other stays as it is, so a
 * half-filled form is not lost.
 */
(() => {
    const clocks = [...document.querySelectorAll(".clock-now")];
    if (clocks.length === 0) return;
    const choices = [...document.querySelectorAll(".time-zone-choice")];
    const PICKED = "ofm_timezone_picked";
    const COOKIE = "ofm_timezone=";
    const year = 60 * 60 * 24 * 365;
    const own = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    // The zone the server drew this page in.
    const drawn = clocks[0].dataset.zone;
    let date;
    let time;

    function picked() {
        try {
            return window.localStorage.getItem(PICKED) === "1";
        } catch {
            return false;
        }
    }

    function remember(isPicked) {
        try {
            if (isPicked) window.localStorage.setItem(PICKED, "1");
            else window.localStorage.removeItem(PICKED);
        } catch {
            // private window: the cookie still holds the zone
        }
    }

    function saved() {
        const found = document.cookie.split("; ").find((part) => part.startsWith(COOKIE));
        return found ? found.slice(COOKIE.length) : "";
    }

    function save(zone) {
        document.cookie = `${COOKIE}${zone}; path=/; max-age=${year}; SameSite=Lax`;
    }

    function tick() {
        const now = new Date();
        for (const clock of clocks) {
            clock.querySelector(".clock-date").textContent = date.format(now);
            clock.querySelector(".clock-time").textContent = time.format(now);
        }
    }

    function show(zone) {
        date = new Intl.DateTimeFormat("en-US", { timeZone: zone, weekday: "long", month: "short", day: "numeric" });
        time = new Intl.DateTimeFormat("en-US", { timeZone: zone, hour: "2-digit", minute: "2-digit", hour12: true });
        const automatic = !picked();
        for (const choice of choices) {
            const current = choice.dataset.zone ? !automatic && choice.dataset.zone === zone : automatic;
            if (current) choice.setAttribute("aria-current", "true");
            else choice.removeAttribute("aria-current");
        }
        tick();
    }

    // A page printing times reloads only when it was drawn in another zone
    // than the one now in use, and only once the cookie really holds it
    // (cookies off: no reload, or it would never end).
    function use(zone) {
        save(zone);
        show(zone);
        if (saved() === zone && drawn !== zone && document.querySelector("[data-zone-times]")) location.reload();
    }

    for (const choice of choices) {
        choice.addEventListener("click", () => {
            remember(Boolean(choice.dataset.zone));
            use(choice.dataset.zone || own || drawn);
        });
    }

    if (!picked() && own && saved() !== own) use(own);
    else show(saved() || drawn);
    setInterval(tick, 1000);
})();
