/*
 * /repurpose page controller.
 *
 * Knobs: every input with `data-knob` is one preset value at that path
 * ("video.zoom", "video.adjust.temp", "audio.pitch"); none has a `name`, so
 * the form never posts them one by one -- submit and Download preset send
 * them as one preset JSON. A range knob is two inputs (min / max): one filled
 * = that number, both = [min, max]. Picking a preset (or Special) fills the
 * knobs and the device from the page's presets (#rp-presets); Special adds
 * the preset's special parts on top.
 *
 * Generate: generation.js posts and polls; a card it builds for a finished
 * or failed run (it carries the ⋯ menu) is swapped for the server's cards
 * (`/repurpose/cards/{job}`: one per copy, with its score, Recreate only).
 *
 * Recreate: the card's video, preset name, Special, knobs, device and copies
 * back in the form.
 */
(() => {
    function number(input) {
        if (!input || input.value.trim() === "") return null;
        const n = Number(input.value);
        return Number.isFinite(n) ? n : null;
    }

    function getPath(obj, path) {
        return path.split(".").reduce((o, key) => (o == null ? undefined : o[key]), obj);
    }

    function setPath(obj, path, value) {
        const keys = path.split(".");
        const last = keys.pop();
        let o = obj;
        for (const key of keys) {
            o[key] = o[key] || {};
            o = o[key];
        }
        o[last] = value;
    }

    // Every knob path once, in page order.
    function paths(form) {
        return [...new Set([...form.querySelectorAll("[data-knob]")].map((el) => el.dataset.knob))];
    }

    function inputs(form, path) {
        return [...form.querySelectorAll(`[data-knob="${path}"]`)];
    }

    function read(form, path) {
        const els = inputs(form, path);
        const first = els[0];
        if (first.dataset.end) {
            const lo = number(els.find((el) => el.dataset.end === "lo"));
            const hi = number(els.find((el) => el.dataset.end === "hi"));
            if (lo === null || hi === null || lo === hi) return lo ?? hi;
            return [Math.min(lo, hi), Math.max(lo, hi)];
        }
        if (first.type === "checkbox" && "switch" in first.dataset) return first.checked || null;
        if (first.type === "checkbox") {
            const words = els.filter((el) => el.checked).map((el) => el.value);
            return words.length ? words : null;
        }
        if (first.tagName === "SELECT") return first.value || null;
        return number(first);
    }

    function write(form, path, value) {
        const els = inputs(form, path);
        const first = els[0];
        if (first.dataset.end) {
            const [lo, hi] = Array.isArray(value) ? value : [value, null];
            for (const el of els) {
                const v = el.dataset.end === "lo" ? lo : hi;
                el.value = v ?? "";
            }
            return;
        }
        if (first.type === "checkbox" && "switch" in first.dataset) {
            first.checked = !!value;
            return;
        }
        if (first.type === "checkbox") {
            const picked = Array.isArray(value) ? value : value ? [value] : [];
            for (const el of els) el.checked = picked.includes(el.value);
            return;
        }
        first.value = value ?? "";
    }

    // The knobs as a preset: only the set ones.
    function collect(form) {
        const preset = { video: {}, audio: {} };
        for (const path of paths(form)) {
            const value = read(form, path);
            if (value !== null) setPath(preset, path, value);
        }
        return preset;
    }

    function fill(form, preset) {
        for (const path of paths(form)) write(form, path, getPath(preset, path));
    }

    function hasOption(select, value) {
        return [...select.options].some((o) => o.value === value);
    }

    function initPresets(form, presets) {
        const select = form.querySelector("#rp-preset");
        const special = form.querySelector("#rp-special");
        const device = form.querySelector("#rp-device");

        function apply() {
            const p = presets[select.value];
            if (!p) return;
            const on = special.checked;
            fill(form, {
                video: { ...p.video, ...(on ? p.video_special : {}) },
                audio: { ...p.audio, ...(on ? p.audio_special : {}) },
            });
            const name = (on && p.device_special) || p.device || form.dataset.defaultDevice;
            if (hasOption(device, name)) device.value = name;
        }

        select.addEventListener("change", apply);
        special.addEventListener("change", apply);
        apply();
    }

    function initSubmit(form) {
        form.addEventListener("submit", (e) => {
            e.preventDefault();
            const fd = window.FilePicker.collectFormData(new FormData(form));
            fd.set("preset", JSON.stringify(collect(form)));
            document.getElementById("rp-empty").hidden = true;
            window.Generation.submit(form, fd);
        });
    }

    function initDownload(form) {
        form.querySelector(".rp-download").addEventListener("click", () => {
            const name = form.querySelector("#rp-preset").value || "custom";
            const special = form.querySelector("#rp-special").checked;
            const preset = {
                ...collect(form),
                label: special ? `${name} special` : name,
                device: form.querySelector("#rp-device").value,
            };
            fetch("/repurpose/preset.json", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(preset),
            })
                .then((r) => {
                    if (!r.ok) throw new Error("preset refused");
                    return r.blob();
                })
                .then((blob) => {
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `repurpose-${name}${special ? "-special" : ""}.json`;
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    URL.revokeObjectURL(url);
                })
                .catch(() => {
                    const status = document.querySelector("#results-panel .generation-status");
                    status.innerHTML = '<div class="generation-error">A knob has a value it cannot take</div>';
                });
        });
    }

    // generation.js's cards (they carry the ⋯ menu) -> the server's.
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
            fetch(`/repurpose/cards/${jobId}`)
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
            const select = form.querySelector("#rp-preset");
            select.value = hasOption(select, params.preset_name || "") ? params.preset_name || "" : "";
            form.querySelector("#rp-special").checked = !!params.special;
            fill(form, params.preset || {});
            const device = form.querySelector("#rp-device");
            if (hasOption(device, params.device)) device.value = params.device;
            if (params.copies) form.querySelector("#rp-copies").value = params.copies;
            form.scrollIntoView({ behavior: "smooth" });
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("repurpose-form");
        const gallery = document.getElementById("rp-gallery");
        if (!form || !gallery) return;
        const presets = JSON.parse(document.getElementById("rp-presets").textContent);
        initPresets(form, presets);
        initSubmit(form);
        initDownload(form);
        initSwap(gallery);
        initRecreate(form, gallery);
    });
})();
