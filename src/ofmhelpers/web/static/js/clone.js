/*
 * /clone page controller.
 *
 * Submit: one POST queues a Wan 3.0 run per video and answers with their
 * cards already rendered (the same _clone_card.html the gallery uses,
 * newest first), so they go in place -- no reload -- and generation.js
 * polls them like any other card. XHR rather than fetch only for upload
 * progress: several videos can take a while to send.
 *
 * Recreate (a card's ⋯ menu, or a failed card's button): the subject
 * images, that card's video and its settings back in the form. Files come
 * back as "existing" picker entries, so nothing is uploaded again.
 *
 * Upload to Drive (a card's ⋯ menu): the picker dialog lists the account
 * folders, fewest files waiting first; search, a model filter, folders the
 * video is already in marked; the last picked ones (and model) are picked
 * again. Each card lists where its video went, polled while uploads run.
 *
 * Prefill: /reels sends the picked reels here as ?video= paths, which the
 * server checks and hands over in data-prefill-videos.
 */
(() => {
    const DRIVE_POLL_MS = 3000;
    // The picker's last choice, per browser.
    const LAST_PICK_KEY = "clone-drive-last";

    function setStatus(statusEl, message, isError) {
        statusEl.innerHTML = "";
        if (!message) return;
        const el = document.createElement("div");
        if (isError) el.className = "generation-error";
        el.textContent = message;
        statusEl.appendChild(el);
    }

    function manifestCount(formData, field) {
        try {
            return JSON.parse(formData.get(`${field}_manifest`) || "[]").length;
        } catch {
            return 0;
        }
    }

    // What the server would reject anyway, caught before any upload starts.
    function validate(form, formData) {
        const maxVideos = Number(form.dataset.maxVideos);
        const maxImages = Number(form.dataset.maxImages);
        const videos = manifestCount(formData, "videos");
        const images = manifestCount(formData, "image");
        if (!String(formData.get("api_key") || "").trim()) return "Enter the API key.";
        if (images < 1) return "Pick at least one subject image.";
        if (images > maxImages) return `Pick at most ${maxImages} subject images.`;
        if (videos < 1) return "Pick at least one video.";
        if (videos > maxVideos) {
            return `Pick at most ${maxVideos} videos (${videos} picked) — each one is a paid run.`;
        }
        if (!String(formData.get("prompt") || "").trim()) return "Enter a prompt.";
        return null;
    }

    function errorMessage(xhr) {
        let data = {};
        try {
            data = JSON.parse(xhr.responseText);
        } catch {
            return `Request failed (${xhr.status}) — try again.`;
        }
        if (typeof data.detail === "string") return data.detail;
        if (Array.isArray(data.detail) && data.detail.length) {
            return data.detail.map((d) => d.msg).join("; ");
        }
        return `Request failed (${xhr.status}).`;
    }

    // A card while the videos upload: the gallery's card shape, a progress
    // bar in place of the media.
    function buildUploadingCard(videoCount) {
        const card = document.createElement("div");
        card.className = "result-item";
        const box = document.createElement("div");
        box.className = "result-file";
        const bar = document.createElement("progress");
        bar.max = 100;
        bar.value = 0;
        const label = document.createElement("p");
        label.className = "source";
        box.append(bar);
        card.append(box, label);
        const noun = videoCount === 1 ? "video" : "videos";
        card.setProgress = (pct) => {
            bar.value = pct;
            label.textContent =
                pct < 100
                    ? `Uploading ${videoCount} ${noun}… ${pct}%`
                    : `Checking ${videoCount} ${noun} and queuing runs…`;
        };
        card.setProgress(0);
        return card;
    }

    function initSubmit(form, gallery) {
        const statusEl = form.querySelector(".generation-status");
        const submitBtn = form.querySelector('button[type="submit"]');
        const empty = document.getElementById("clone-empty");
        let uploading = false;
        const showEmpty = () => {
            empty.hidden = gallery.querySelector(".result-item") !== null;
        };

        window.addEventListener("beforeunload", (e) => {
            if (uploading) e.preventDefault();
        });

        form.addEventListener("submit", (e) => {
            e.preventDefault();
            if (uploading) return;
            const formData = window.FilePicker.collectFormData(new FormData(form));
            const invalid = validate(form, formData);
            if (invalid) {
                setStatus(statusEl, invalid, true);
                return;
            }

            uploading = true;
            submitBtn.disabled = true;
            setStatus(statusEl, "");
            empty.hidden = true;
            const pending = buildUploadingCard(manifestCount(formData, "videos"));
            gallery.prepend(pending);

            const done = () => {
                uploading = false;
                submitBtn.disabled = false;
            };
            const xhr = new XMLHttpRequest();
            xhr.open("POST", form.action);
            xhr.upload.addEventListener("progress", (ev) => {
                if (ev.lengthComputable) {
                    pending.setProgress(Math.floor((ev.loaded / ev.total) * 100));
                }
            });
            xhr.addEventListener("load", () => {
                done();
                if (xhr.status === 401) {
                    // session.js only wraps fetch; same redirect for XHR.
                    const login = JSON.parse(xhr.responseText || "{}").login_url;
                    window.location.href = login || "/login";
                    return;
                }
                if (xhr.status < 200 || xhr.status >= 300) {
                    pending.remove();
                    showEmpty();
                    setStatus(statusEl, errorMessage(xhr), true);
                    return;
                }
                const data = JSON.parse(xhr.responseText);
                const holder = document.createElement("div");
                holder.innerHTML = data.html;
                const cards = [...holder.children];
                // live-gallery.js may have drawn some already while the
                // request was still in flight.
                cards.forEach((card) => {
                    gallery.querySelector(`[data-job-id="${card.dataset.jobId}"]`)?.remove();
                });
                pending.replaceWith(...cards);
                window.Generation.resumePendingCards();
                // The images usually stay for the next run; the videos don't.
                window.FilePicker.clearPicker(
                    form.querySelector('.file-picker[data-field="videos"]')
                );
                const n = data.job_ids.length;
                setStatus(statusEl, `Queued ${n} run${n === 1 ? "" : "s"}.`, false);
            });
            xhr.addEventListener("error", () => {
                done();
                pending.remove();
                showEmpty();
                setStatus(statusEl, "Upload failed — check your connection and try again.", true);
            });
            xhr.send(formData);
        });
    }

    // The form visibly fills; no message on top of it.
    function fillForm(form, params) {
        window.FilePicker.setExisting("image", params.reference_images || []);
        window.FilePicker.setExisting("videos", params.reference_videos || []);
        const prompt = form.querySelector('[name="prompt"]');
        prompt.value = params.prompt || "";
        prompt.dispatchEvent(new Event("input", { bubbles: true }));
        if (params.resolution) form.querySelector('[name="resolution"]').value = params.resolution;
        const provider = form.querySelector('[name="provider"]');
        if (provider && params.provider && provider.value !== params.provider
            && provider.querySelector(`option[value="${params.provider}"]`)) {
            provider.value = params.provider;
            provider.dispatchEvent(new Event("change"));
        }
        form.querySelector('[name="audio"]').checked = !!params.audio;
        form.querySelector('[name="export"]').value =
            params.export || (params.capcut ? "capcut" : "original");
        form.querySelector('[name="mirror"]').checked = !!params.mirror;
        setStatus(form.querySelector(".generation-status"), "", false);
        form.scrollIntoView({ behavior: "smooth" });
    }

    function initPrefill(form) {
        const videos = JSON.parse(form.dataset.prefillVideos || "[]");
        if (!videos.length) return;
        window.FilePicker.setExisting("videos", videos);
    }

    function initRecreate(form, gallery) {
        gallery.addEventListener("click", (e) => {
            if (!e.target.closest(".recreate-btn")) return;
            const card = e.target.closest(".result-item[data-params]");
            if (card) fillForm(form, JSON.parse(card.dataset.params));
        });
    }

    // --- where each card's video went in Drive ---------------------------------

    function driveStateItem(upload) {
        const li = document.createElement("li");
        li.dataset.status = upload.status;
        li.dataset.login = upload.login_id;
        if (upload.status === "done") {
            li.append("In Drive: ");
            const a = document.createElement("a");
            a.href = upload.drive_url;
            a.target = "_blank";
            a.rel = "noopener";
            a.textContent = upload.folder_name;
            li.appendChild(a);
        } else if (upload.status === "failed") {
            const span = document.createElement("span");
            span.title = upload.error || "";
            span.textContent = `Drive upload to ${upload.folder_name} failed`;
            li.appendChild(span);
        } else {
            const spinner = document.createElement("span");
            spinner.className = "spinner";
            li.append(spinner, ` Uploading to ${upload.folder_name}…`);
        }
        return li;
    }

    function showDriveStates(uploads) {
        const byJob = {};
        for (const upload of uploads) {
            if (!byJob[upload.job_id]) byJob[upload.job_id] = [];
            byJob[upload.job_id].push(upload);
        }
        for (const [jobId, list] of Object.entries(byJob)) {
            const card = document.querySelector(`.result-item[data-job-id="${jobId}"]`);
            const states = card?.querySelector(".drive-states");
            if (states) states.replaceChildren(...list.map(driveStateItem));
        }
    }

    function pollDrive(jobIds) {
        const query = jobIds.map((id) => `job=${encodeURIComponent(id)}`).join("&");
        fetch(`/clone/drive-uploads?${query}`)
            .then((r) => (r.ok ? r.json() : []))
            .then((uploads) => {
                showDriveStates(uploads);
                if (uploads.some((u) => u.status === "queued")) {
                    setTimeout(() => pollDrive(jobIds), DRIVE_POLL_MS);
                }
            })
            .catch(() => setTimeout(() => pollDrive(jobIds), DRIVE_POLL_MS));
    }

    // A card that finished while the page was open gets what a reloaded
    // one has: its Drive states and "Upload to Drive" in the menu.
    function decorateFinished(card) {
        if (!card.matches(".result-item") || card.querySelector(".drive-states")) return;
        const states = document.createElement("ul");
        states.className = "drive-states";
        card.querySelector(".result-actions")?.before(states);
        const item = document.getElementById("card-drive-item");
        const list = card.querySelector(".menu-list");
        if (item && list) list.append(item.content.firstElementChild.cloneNode(true));
    }

    // --- the Drive picker ------------------------------------------------------

    function readLastPick() {
        try {
            return JSON.parse(localStorage.getItem(LAST_PICK_KEY) || "{}");
        } catch {
            return {};
        }
    }

    function saveLastPick(pick) {
        try {
            localStorage.setItem(LAST_PICK_KEY, JSON.stringify(pick));
        } catch {
            // private window: the next pick just starts empty
        }
    }

    function initDrivePicker(gallery) {
        const dialog = document.getElementById("drive-picker");
        if (!dialog) return;
        const form = document.getElementById("drive-picker-form");
        const search = document.getElementById("drive-picker-search");
        const model = document.getElementById("drive-picker-model");
        const error = document.getElementById("drive-picker-error");
        const submit = document.getElementById("drive-picker-submit");
        const rows = [...dialog.querySelectorAll(".drive-pick")];
        let jobId = null;

        const box = (row) => row.querySelector("input");
        const picked = () => rows.filter((row) => !row.hidden && box(row).checked && !box(row).disabled);

        function refresh() {
            const n = picked().length;
            submit.disabled = n === 0;
            submit.textContent = n ? `Upload to ${n} account${n === 1 ? "" : "s"}` : "Upload";
        }

        function filter() {
            const needle = search.value.trim().replace(/^@/, "").toLowerCase();
            rows.forEach((row) => {
                row.hidden = (model.value && row.dataset.creator !== model.value)
                    || (needle && !row.dataset.search.includes(needle));
            });
            refresh();
        }

        function open(card) {
            jobId = card.dataset.jobId;
            const already = new Set(
                [...card.querySelectorAll('.drive-states li:not([data-status="failed"])')]
                    .map((li) => li.dataset.login)
            );
            const last = readLastPick();
            const lastLogins = new Set((last.logins || []).map(String));
            model.value = [...model.options].some((o) => o.value === last.model) ? last.model : "";
            search.value = "";
            rows.forEach((row) => {
                const done = already.has(row.dataset.login);
                box(row).disabled = done;
                box(row).checked = done || lastLogins.has(row.dataset.login);
                row.querySelector(".drive-pick-done").hidden = !done;
            });
            error.hidden = true;
            filter();
            dialog.showModal();
        }

        gallery.addEventListener("click", (e) => {
            const btn = e.target.closest(".drive-upload-btn");
            if (btn) open(btn.closest(".result-item"));
        });
        search.addEventListener("input", filter);
        model.addEventListener("change", filter);
        rows.forEach((row) => box(row).addEventListener("change", refresh));
        document.getElementById("drive-picker-cancel").addEventListener("click", () => dialog.close());

        form.addEventListener("submit", (e) => {
            e.preventDefault();
            const logins = picked().map((row) => Number(row.dataset.login));
            if (!logins.length || !jobId) return;
            submit.disabled = true;
            fetch("/clone/drive-upload", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ job_ids: [jobId], login_ids: logins }),
            })
                .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
                .then(({ ok, body }) => {
                    if (!ok) throw new Error(body.detail || "Upload refused");
                    saveLastPick({ logins, model: model.value });
                    dialog.close();
                    showDriveStates(body.uploads);
                    pollDrive([jobId]);
                })
                .catch((err) => {
                    error.textContent = err.message;
                    error.hidden = false;
                })
                .finally(refresh);
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("clone-form");
        const gallery = document.getElementById("clone-gallery");
        gallery.addEventListener("gallery:card-ready", (e) => decorateFinished(e.target));
        initSubmit(form, gallery);
        initRecreate(form, gallery);
        initPrefill(form);
        initDrivePicker(gallery);
        // Uploads still on their way when the page opened.
        const pending = [...gallery.querySelectorAll('.drive-states li[data-status="queued"]')]
            .map((li) => li.closest(".result-item").dataset.jobId);
        if (pending.length) pollDrive([...new Set(pending)]);
    });
})();
