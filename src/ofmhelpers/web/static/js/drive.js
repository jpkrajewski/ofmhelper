/*
 * /drive page controller.
 *
 *   search box -> filters the cards instantly (their data-search text)
 *   Sync       -> POST /drive/sync (makes today's missing folders), then
 *                 reload; a failure stays on screen
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
    if (search) {
        search.addEventListener("input", () => {
            const words = search.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
            for (const card of document.querySelectorAll(".dr-card")) {
                const text = card.dataset.search || "";
                card.hidden = !words.every((w) => text.includes(w));
            }
        });
    }

    function summary(report) {
        if (report.busy) return "A sync is already running";
        const parts = [];
        if (report.created.length) parts.push(`${report.created.length} folders created`);
        if (report.adopted.length) parts.push(`${report.adopted.length} found`);
        if (report.renamed.length) parts.push(`${report.renamed.length} renamed`);
        if (report.days) parts.push(`${report.days} day folders`);
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
