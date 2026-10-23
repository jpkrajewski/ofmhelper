/*
 * /download-assets: hand a finished download's video to /clone or
 * /clone-long. The card's [Clone | Clone long] posts the job id; the server
 * resolves that download's video file(s) and answers the URL to open with
 * them already picked (clone: every video, clone-long: the first).
 *
 * A reloaded card carries the control from the server
 * (_download_gallery_card.html); a card that finishes while the page is open
 * has it cloned in on gallery:card-ready -- its video is what decides it,
 * the same test the server makes.
 */
(() => {
    function targetOf(btn) {
        return btn.classList.contains("send-to-long") ? "clone-long" : "clone";
    }

    async function send(btn) {
        const card = btn.closest(".result-item[data-job-id]");
        if (!card) return;
        const buttons = card.querySelectorAll(".send-to-clone, .send-to-long");
        buttons.forEach((b) => {
            b.disabled = true;
        });
        try {
            const body = new URLSearchParams({
                job_id: card.dataset.jobId,
                target: targetOf(btn),
            });
            const r = await fetch("/download-media/send", { method: "POST", body });
            if (!r.ok) throw new Error((await r.text()) || r.status);
            const { redirect } = await r.json();
            window.location = redirect;
        } catch (err) {
            buttons.forEach((b) => {
                b.disabled = false;
            });
            console.error("send to clone failed", err);
        }
    }

    // A live-finished video download: give it the same control reloaded cards
    // already have.
    function decorate(card) {
        if (card.dataset.task !== "download_media") return;
        if (card.querySelector(".send-to")) return;
        if (!card.querySelector("video.result-video")) return;
        const template = document.getElementById("card-send-to");
        const meta = card.querySelector(".source");
        if (!template || !meta) return;
        const node = template.content.firstElementChild.cloneNode(true);
        meta.insertAdjacentElement("afterend", node);
    }

    document.addEventListener("DOMContentLoaded", () => {
        const panel = document.getElementById("results-panel");
        if (!panel) return;
        panel.addEventListener("click", (e) => {
            const btn = e.target.closest(".send-to-clone, .send-to-long");
            if (btn) send(btn);
        });
        const gallery = panel.querySelector(".results");
        if (gallery) {
            gallery.addEventListener("gallery:card-ready", (e) => decorate(e.target));
        }
    });
})();
