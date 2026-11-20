(() => {
    function initLiveGallery(container) {
        const endpoint = container.dataset.liveEndpoint;
        const key = container.dataset.liveKey;
        const intervalMs = Number(container.dataset.liveInterval) * 1000;
        if (!endpoint || !key || !(intervalMs > 0)) return;
        const empty = document.getElementById(container.dataset.liveEmpty || "");

        let timer = null;
        let inFlight = false;

        function existing(id) {
            return Array.from(container.children).find((el) => el.dataset[key] === id);
        }

        function merge(html) {
            const holder = document.createElement("div");
            holder.innerHTML = html;

            const items = Array.from(holder.children).filter((el) => el.dataset[key]);
            const known = items.findIndex((el) => existing(el.dataset[key]));
            const fresh = known === -1 ? items : items.slice(0, known);
            if (!fresh.length) return;
            container.prepend(...fresh);
            if (empty) empty.hidden = true;
            window.Generation.resumePendingCards();
            container.dispatchEvent(new CustomEvent("live-gallery:update"));
        }

        function schedule() {
            clearTimeout(timer);
            timer = document.hidden ? null : setTimeout(tick, intervalMs);
        }

        function tick() {
            timer = null;
            if (inFlight) return;
            if (container.hasAttribute("data-live-paused")) {
                schedule();
                return;
            }
            inFlight = true;
            fetch(endpoint)
                .then((r) => {
                    if (!r.ok) throw new Error(`live gallery: ${r.status}`);
                    return r.text();
                })
                .then(merge)

                .catch(() => {})
                .finally(() => {
                    inFlight = false;
                    schedule();
                });
        }

        container.addEventListener("live-gallery:refresh", () => {
            if (!document.hidden) tick();
        });
        document.addEventListener("visibilitychange", () => {
            if (document.hidden) {
                clearTimeout(timer);
                timer = null;
            } else {
                tick();
            }
        });
        schedule();
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("[data-live-endpoint]").forEach(initLiveGallery);
    });
})();
