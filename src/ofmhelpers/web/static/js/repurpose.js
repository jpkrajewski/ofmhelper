(() => {
    function initSwap(gallery) {
        const swapping = new Set();

        function swap(card) {
            const jobId = card.dataset.jobId;
            if (!jobId) {
                card.querySelector(".menu")?.remove();
                return;
            }
            if (swapping.has(jobId)) return;
            swapping.add(jobId);
            fetch(`/spoof/cards/${jobId}`)
                .then((r) => {
                    if (!r.ok) throw new Error("cards failed");
                    return r.text();
                })
                .then((html) => {
                    const tpl = document.createElement("template");
                    tpl.innerHTML = html.trim();
                    const old = gallery.querySelectorAll(`.result-item[data-job-id="${jobId}"]`);
                    if (!old.length) return;
                    old[0].before(...tpl.content.children);
                    old.forEach((el) => el.remove());
                })
                .catch(() => swapping.delete(jobId));
        }

        new MutationObserver((records) => {
            for (const record of records) {
                for (const node of record.addedNodes) {
                    if (node.nodeType === 1 && node.matches(".result-item") && node.querySelector(".menu")) {
                        swap(node);
                    }
                }
            }
        }).observe(gallery, { childList: true });
    }

    function initRecreate(form, gallery) {
        gallery.addEventListener("click", (e) => {
            if (!e.target.closest(".recreate-btn")) return;
            const card = e.target.closest(".result-item[data-params]");
            if (!card) return;
            const params = JSON.parse(card.dataset.params);
            window.FilePicker.setExisting("video", params.source ? [params.source] : []);
            form.scrollIntoView({ behavior: "smooth" });
        });
    }

    function initDrive(gallery) {
        gallery.addEventListener("click", (e) => {
            const upload = e.target.closest(".drive-upload-btn");
            const jobId = upload?.closest(".result-item")?.dataset.jobId;
            if (jobId) window.location.href = `/drive?upload=${encodeURIComponent(jobId)}`;

            const remove = e.target.closest(".drive-remove");
            if (!remove || !window.confirm(`${remove.getAttribute("aria-label")}?`)) return;
            remove.disabled = true;
            fetch(`/clone/drive-uploads/${remove.dataset.upload}/remove`, { method: "POST" })
                .then(async (r) => {
                    if (!r.ok) {
                        const body = await r.json().catch(() => ({}));
                        throw new Error(body.detail || `failed (${r.status})`);
                    }
                    window.location.reload();
                })
                .catch((err) => {
                    remove.disabled = false;
                    window.alert(`Could not remove it: ${err.message}`);
                });
        });
    }

    const DRIVE_POLL_MS = 3000;
    const DRIVE_POLL_MAX_MS = 30000;

    function followUploads(gallery, wait = DRIVE_POLL_MS) {
        const queued = [...gallery.querySelectorAll('.drive-states li[data-status="queued"]')]
            .map((li) => li.closest(".result-item")?.dataset.jobId)
            .filter(Boolean);
        if (!queued.length) return;
        let refused = false;
        const redraws = [...new Set(queued)].map((jobId) =>
            fetch(`/spoof/cards/${jobId}`)
                .then((r) => {
                    if (!r.ok) refused = true;
                    return r.ok ? r.text() : "";
                })
                .then((html) => {
                    const tpl = document.createElement("template");
                    tpl.innerHTML = html.trim();
                    const fresh = tpl.content.querySelector(".drive-states");
                    const shown = gallery.querySelector(`.result-item[data-job-id="${jobId}"] .drive-states`);
                    if (fresh && shown) shown.replaceWith(fresh);
                })
        );
        Promise.allSettled(redraws).then(() => {
            if (refused) return;
            const next = Math.min(wait * 1.5, DRIVE_POLL_MAX_MS);
            setTimeout(() => followUploads(gallery, next), wait);
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("repurpose-form");
        const gallery = document.getElementById("rp-gallery");
        if (!form || !gallery) return;
        initSwap(gallery);
        initRecreate(form, gallery);
        initDrive(gallery);
        followUploads(gallery);
        const prefill = JSON.parse(form.dataset.prefillVideo || "[]");
        if (prefill.length) window.FilePicker.setExisting("video", prefill);
    });
})();
