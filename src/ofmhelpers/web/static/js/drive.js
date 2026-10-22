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

    // The page shows the last read at once and swaps in Drive's live read
    // silently; only a failed live read says anything.
    const board = $("dr-board");
    if (board?.dataset.live) {
        const asOf = board.querySelector(".dr-asof");
        fetch(board.dataset.live)
            .then((r) => {
                if (!r.ok) throw new Error(`Drive did not answer (${r.status})`);
                return r.text();
            })
            .then((html) => {
                board.innerHTML = html;
                delete board.dataset.live;
                window.Layout?.apply();
                if (search) applySearch();
            })
            .catch((err) => {
                if (!asOf) return;
                asOf.textContent = `Showing the last read: ${err.message}`;
                asOf.hidden = false;
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
