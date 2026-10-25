/*
 * Live gallery: shows runs other sessions start, without a reload.
 *
 * A page only ever learned about the cards rendered at load and its own
 * submits, so a coworker's generation stayed invisible until a refresh. Any
 * `[data-live-endpoint]` container polls that endpoint for its newest items
 * (the same server partial the page renders) and prepends the ones newer
 * than every item it has, matched on `data-live-key` -- a dataset name
 * ("jobId": every gallery). An older one it lacks is not new: one pulled up
 * into the newest page because a card above it was hidden
 * (hidden-cards.js) belongs further down, where scrolling reaches it.
 *
 * An item already on the page is left alone -- its own poller
 * (generation.js) keeps it current.
 *
 * Paused while the tab is hidden, refreshed as soon as it is shown again,
 * and while the container is `data-live-paused` (hidden-cards.js's view of
 * only the hidden cards: new runs belong in the normal one).
 * Fires "live-gallery:update" on the container after a change.
 */
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
            // Newest first: the ones above the first item already shown.
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
                // A missed poll is retried on the next tick; session.js
                // already turns an expired session into a redirect.
                .catch(() => {})
                .finally(() => {
                    inFlight = false;
                    schedule();
                });
        }

        // A page asks for a poll now (clone-links.js after a send whose
        // answer was lost: the runs may be queued already).
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
