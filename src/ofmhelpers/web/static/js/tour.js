(() => {
    const GAP = 12;
    const EDGE = 6;

    const FOLLOW_MS = 250;

    function shownFor(key) {
        try {
            return window.localStorage.getItem(key);
        } catch {
            return null;
        }
    }

    function remember(key, login) {
        try {
            window.localStorage.setItem(key, login);
        } catch {

        }
    }

    function shown(selector) {
        const found = document.querySelector(selector);
        return found && found.getClientRects().length > 0 ? found : null;
    }

    document.addEventListener("DOMContentLoaded", () => {
        const tip = document.getElementById("tour-tip");
        if (!tip) return;
        const next = tip.querySelector(".tour-next");
        const shade = document.createElement("div");
        shade.className = "tour-shade";
        shade.hidden = true;
        document.body.append(shade);
        let steps = [];
        let at = -1;
        let following = 0;

        function found() {
            return [...tip.querySelectorAll("li[data-target]")]
                .map((item) => ({ selector: item.dataset.target, text: item.textContent }))
                .filter((step) => shown(step.selector));
        }

        function beside(box) {
            const wide = tip.offsetWidth;
            const tall = tip.offsetHeight;
            const maxLeft = window.innerWidth - wide - GAP;
            const maxTop = window.innerHeight - tall - GAP;
            const within = (value, most) => Math.max(GAP, Math.min(value, most));
            const left = within(box.left, maxLeft);
            if (box.bottom + GAP + tall <= window.innerHeight) return { top: box.bottom + GAP, left };
            if (box.top - GAP - tall >= 0) return { top: box.top - GAP - tall, left };
            if (box.right + GAP + wide <= window.innerWidth) {
                return { top: within(box.top, maxTop), left: box.right + GAP };
            }
            return { top: maxTop, left };
        }

        function place() {
            if (at < 0) return;
            const target = shown(steps[at].selector);
            if (!target) return;
            const box = target.getBoundingClientRect();
            shade.style.setProperty("top", `${box.top - EDGE}px`);
            shade.style.setProperty("left", `${box.left - EDGE}px`);
            shade.style.setProperty("width", `${box.width + 2 * EDGE}px`);
            shade.style.setProperty("height", `${box.height + 2 * EDGE}px`);
            const spot = beside(box);
            tip.style.setProperty("top", `${spot.top}px`);
            tip.style.setProperty("left", `${spot.left}px`);
        }

        function end() {
            window.clearInterval(following);
            at = -1;
            shade.hidden = true;
            tip.hidden = true;
        }

        function show(index) {
            const target = shown(steps[index].selector);
            if (!target) {

                if (index < steps.length - 1) show(index + 1);
                else end();
                return;
            }
            at = index;
            tip.querySelector(".tour-count").textContent = `${at + 1} / ${steps.length}`;
            tip.querySelector(".tour-text").textContent = steps[at].text;
            next.textContent = at === steps.length - 1 ? "Done" : "Next";
            shade.hidden = false;
            tip.hidden = false;

            const tall = target.getBoundingClientRect().height > window.innerHeight * 0.7;
            target.scrollIntoView({ block: tall ? "start" : "center" });
            place();
        }

        function start() {
            steps = found();
            if (steps.length === 0) return;
            window.clearInterval(following);
            following = window.setInterval(place, FOLLOW_MS);
            show(0);
        }

        next.addEventListener("click", () => {
            if (at < steps.length - 1) show(at + 1);
            else end();
        });
        tip.querySelector(".tour-skip").addEventListener("click", end);
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && at >= 0) end();
        });
        window.addEventListener("scroll", place, { passive: true, capture: true });
        window.addEventListener("resize", place);
        document.querySelector(".tour-open")?.addEventListener("click", start);

        const key = `tour:${window.location.pathname}`;
        const login = tip.dataset.login;
        if ("auto" in tip.dataset && login && shownFor(key) !== login) {
            start();
            remember(key, login);
        }
    });
})();
