/*
 * Live gallery: shows runs other sessions start, without a reload.
 *
 * A page only ever learned about the cards rendered at load and its own
 * submits, so a coworker's generation stayed invisible until a refresh. Any
 * `[data-live-endpoint]` container polls that endpoint for its newest items
 * (the same server partial the page renders) and prepends the ones it lacks,
 * matched on `data-live-key` -- a dataset name: "jobId" for /generate cards,
 * "batch" for /clone batches.
 *
 * An item already on the page is left alone -- its own poller
 * (generation.js) keeps it current -- unless the fresh copy holds more jobs:
 * a /clone batch caught while its submit was still queuing runs arrives
 * partial, and is swapped for the complete one on the next poll.
 *
 * Paused while the tab is hidden, refreshed as soon as it is shown again.
 * Fires "live-gallery:update" on the container after a change, for pages
 * that post-process their markup (clone.js formats batch times).
 */
(function () {
    function jobCount(el) {
        return el.querySelectorAll("[data-job-id]").length;
    }

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
            const fresh = [];
            let changed = false;
            for (const el of Array.from(holder.children)) {
                const id = el.dataset[key];
                if (!id) continue;
                const current = existing(id);
                if (!current) {
                    fresh.push(el);
                } else if (jobCount(el) > jobCount(current)) {
                    current.replaceWith(el);
                    changed = true;
                }
            }
            if (fresh.length) {
                container.prepend(...fresh);
                if (empty) empty.hidden = true;
                changed = true;
            }
            if (!changed) return;
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
