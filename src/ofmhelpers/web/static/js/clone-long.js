/*
 * /clone-long: the picked video's price, live. Its length is read in the
 * browser (video metadata), split like web/clone_long/models.py Plan (the
 * fewest pieces of at most data-max-piece seconds), priced at the picked
 * provider's credits per second for the picked resolution. Over
 * data-max-video it says so. The server measures and prices again on Start;
 * this is only the preview.
 */
(() => {
    const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
    const credits = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

    function readLength(item) {
        return new Promise((resolve) => {
            const video = document.createElement("video");
            video.preload = "metadata";
            const src = item.file
                ? URL.createObjectURL(item.file)
                : `/refs/file?path=${encodeURIComponent(item.path)}`;
            const done = (seconds) => {
                if (item.file) URL.revokeObjectURL(src);
                video.removeAttribute("src");
                resolve(seconds);
            };
            video.addEventListener("loadedmetadata", () => done(video.duration));
            video.addEventListener("error", () => done(Number.NaN));
            video.src = src;
        });
    }

    function init(form) {
        const picker = form.querySelector('.file-picker[data-field="video"]');
        const provider = form.querySelector('[name="provider"]');
        const resolution = form.querySelector('[name="resolution"]');
        const quote = document.getElementById("clone-long-quote");
        const rates = JSON.parse(form.dataset.rates || "{}");
        const maxPiece = Number(form.dataset.maxPiece);
        const maxVideo = Number(form.dataset.maxVideo);
        let seconds = null;
        // Which pick the last read was for: a slow read of an earlier pick
        // (a big file) must not overwrite the current one's length.
        let pick = 0;

        function render() {
            const rate = rates[provider.value];
            quote.hidden = seconds === null || !rate;
            if (quote.hidden) return;
            if (!Number.isFinite(seconds)) {
                quote.textContent = "Could not read the video's length";
                return;
            }
            if (seconds > maxVideo) {
                quote.textContent = `${seconds.toFixed(0)} s · the longest is ${maxVideo} s`;
                return;
            }
            const pieces = seconds > maxPiece ? Math.ceil(seconds / maxPiece) : 1;
            const total = seconds * (rate.credits_per_s[resolution.value] || 0);
            const noun = pieces === 1 ? "piece" : "pieces";
            quote.textContent =
                `${seconds.toFixed(0)} s · ${pieces} ${noun} · ` +
                `${usd.format(total * rate.usd_per_credit)} · ${credits.format(total)} credits`;
        }

        function update() {
            const [item] = window.FilePicker.items(picker);
            const current = ++pick;
            if (!item) {
                seconds = null;
                render();
                return;
            }
            readLength(item).then((length) => {
                if (current !== pick) return;
                seconds = length;
                render();
            });
        }

        // A started run's card goes on top: the empty text goes.
        form.addEventListener("submit", () => {
            const empty = document.getElementById("clone-long-empty");
            if (empty) empty.hidden = true;
        });
        picker.addEventListener("filepicker:change", update);
        provider.addEventListener("change", render);
        resolution.addEventListener("change", render);
        // A download sent its video here (?video=): open with it picked.
        const prefill = JSON.parse(form.dataset.prefillVideo || "[]");
        if (prefill.length) window.FilePicker.setExisting("video", prefill);
        update();
    }

    // Recreate: a run's image, video, prompt, resolution and provider back in
    // the form (2026-10-23: the button did nothing here). A file no longer on
    // the server is left out and named (FilePicker.setExisting).
    function fill(form, params) {
        window.FilePicker.setExisting("image", params.image ? [params.image] : []);
        window.FilePicker.setExisting("video", params.video ? [params.video] : []);
        const prompt = form.querySelector('[name="prompt"]');
        if (params.prompt) prompt.value = params.prompt;
        const resolution = form.querySelector('[name="resolution"]');
        if (params.resolution) resolution.value = params.resolution;
        const provider = form.querySelector('[name="provider"]');
        if (
            params.provider &&
            provider.value !== params.provider &&
            provider.querySelector(`option[value="${params.provider}"]`)
        ) {
            provider.value = params.provider;
            provider.dispatchEvent(new Event("change"));
        }
        resolution.dispatchEvent(new Event("change"));
        form.scrollIntoView({ behavior: "smooth" });
    }

    function initRecreate(form) {
        document.getElementById("results-panel").addEventListener("click", (e) => {
            if (!e.target.closest(".recreate-btn")) return;
            const card = e.target.closest(".result-item[data-params]");
            if (card) fill(form, JSON.parse(card.dataset.params));
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("clone-long-form");
        if (!form) return;
        init(form);
        initRecreate(form);
    });
})();
