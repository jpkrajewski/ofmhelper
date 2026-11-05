/*
 * The header's clock (`ui.clock_now`): the date and time now in the chosen
 * zone. The globe's menu (`ui.time_zone_menu`) chooses another and remembers it
 * in the `ofm_timezone` cookie (schemas/time_zones.py). A page printing times in
 * that zone (`data-zone-times`) reloads to follow; any other stays as it is, so
 * a half-filled form is not lost.
 */
(() => {
    const clocks = [...document.querySelectorAll(".clock-now")];
    if (clocks.length === 0) return;
    const choices = [...document.querySelectorAll(".time-zone-choice")];
    const year = 60 * 60 * 24 * 365;
    let date;
    let time;

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
        for (const choice of choices) {
            if (choice.dataset.zone === zone) choice.setAttribute("aria-current", "true");
            else choice.removeAttribute("aria-current");
        }
        tick();
    }

    for (const choice of choices) {
        choice.addEventListener("click", () => {
            document.cookie = `ofm_timezone=${choice.dataset.zone}; path=/; max-age=${year}; SameSite=Lax`;
            show(choice.dataset.zone);
            if (document.querySelector("[data-zone-times]")) location.reload();
        });
    }

    show(clocks[0].dataset.zone);
    setInterval(tick, 1000);
})();
