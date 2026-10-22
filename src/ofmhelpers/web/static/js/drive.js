/*
 * /drive page controller.
 *
 *   load       -> the page opened from each folder's last read; fetch
 *                 /drive/live (Drive now, ~2 s) and swap it in
 *   search box -> filters the cards instantly (their data-search text)
 *   Sync       -> POST /drive/sync (makes missing account and POSTED
 *                 folders, records what was posted), then reload; a
 *                 failure stays on screen
 *   a preview Drive has no image for (yet) -> its icon instead
 */
(() => {

    const $ = (id) => document.getElementById(id);
    const statusEl = $("dr-status");

    function setStatus(text, isError) {
        statusEl.innerHTML = "";
        if (!text) return;
        const line = document.createElement("div");
        if (isError) line.className = "generation-error";
        line.textContent = text;
        statusEl.appendChild(line);
    }

    const search = $("dr-search");

    function applySearch() {
        const words = search.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
        for (const card of document.querySelectorAll(".dr-card")) {
            const text = card.dataset.search || "";
            card.hidden = !words.every((w) => text.includes(w));
        }
    }

    if (search) search.addEventListener("input", applySearch);

    // "Last read 3 min ago · refreshing…" while the live read is on its way.
    function ago(ts) {
        const minutes = Math.round((Date.now() / 1000 - ts) / 60);
        if (minutes < 1) return "just now";
        if (minutes < 60) return `${minutes} min ago`;
        return `${Math.round(minutes / 60)} h ago`;
    }

    const board = $("dr-board");
    if (board?.dataset.live) {
        const asOf = board.querySelector(".dr-asof");
        const ts = Number(asOf?.dataset.asOf);
        if (asOf && ts) asOf.textContent = `Last read ${ago(ts)} · refreshing from Drive…`;
        fetch(board.dataset.live)
            .then((r) => {
                if (!r.ok) throw new Error(`Drive did not answer (${r.status})`);
                return r.text();
            })
            .then((html) => {
                board.innerHTML = html;
                delete board.dataset.live;
                if (search) applySearch();
            })
            .catch((err) => {
                if (asOf) asOf.textContent = `Showing the last read: ${err.message}`;
            });
    }

    function summary(report) {
        if (report.busy) return "A sync is already running";
        const parts = [];
        if (report.created.length) parts.push(`${report.created.length} folders created`);
        if (report.adopted.length) parts.push(`${report.adopted.length} found`);
        if (report.renamed.length) parts.push(`${report.renamed.length} renamed`);
        if (report.posted_folders.length) parts.push(`${report.posted_folders.length} POSTED folders set up`);
        if (report.newly_posted) parts.push(`${report.newly_posted} newly posted`);
        if (report.not_connected.length) {
            parts.push(`not connected to Google Drive yet: ${report.not_connected.join(", ")}`);
        }
        return parts.length ? parts.join(", ") : "Everything was there already";
    }

    const syncBtn = $("dr-sync");
    if (syncBtn) {
        syncBtn.addEventListener("click", async () => {
            syncBtn.disabled = true;
            setStatus("Syncing with Drive…");
            try {
                const response = await fetch("/drive/sync", { method: "POST" });
                const data = await response.json().catch(() => null);
                if (!response.ok) {
                    throw new Error(data?.detail || `Sync failed (${response.status})`);
                }
                if (data.failed.length) {
                    setStatus(`${summary(data)}. Failed: ${data.failed.join("; ")}`, true);
                    syncBtn.disabled = false;
                    return;
                }
                setStatus(summary(data));
                window.location.reload();
            } catch (err) {
                setStatus(err.message, true);
                syncBtn.disabled = false;
            }
        });
    }

    // Images load after this script runs: listen in the capture phase.
    document.addEventListener("error", (event) => {
        const img = event.target;
        if (img instanceof HTMLImageElement && img.closest(".dr-file")) img.remove();
    }, true);
})();
