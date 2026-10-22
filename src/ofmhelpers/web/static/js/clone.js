/*
 * /clone page controller.
 *
 * Submit: one POST queues a Wan 3.0 run per video and answers with the new
 * batch already rendered (the same _clone_batch.html partial the page uses),
 * so it is inserted in place -- no reload -- and generation.js polls its
 * cards like any other. XHR rather than fetch only for upload progress:
 * several videos can take a while to send.
 *
 * Recreate: on a batch header it restores the images, every video and the
 * settings; on a single result, the images and just that video. Files come
 * back as "existing" picker entries, so nothing is uploaded again.
 *
 * Prefill: /reels sends the picked reels here as ?video= paths, which the
 * server checks and hands over in data-prefill-videos.
 */
(() => {
    function setStatus(statusEl, message, isError) {
        statusEl.innerHTML = "";
        if (!message) return;
        const el = document.createElement("div");
        if (isError) el.className = "generation-error";
        el.textContent = message;
        statusEl.appendChild(el);
    }

    // "45s", "4m 12s", "43h 48m": readable at a glance at any length.
    function clock(seconds) {
        const s = Math.max(0, Math.round(seconds));
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        if (h) return `${h}h ${m}m`;
        if (m) return `${m}m ${s % 60}s`;
        return `${s}s`;
    }

    // A running card's time since submit, against the estimate from recent
    // runs (clone_eta.py) when there is one. Every second, so it counts live.
    function tickTimers() {
        const now = Date.now() / 1000;
        document.querySelectorAll(".clone-timer[data-started-at]").forEach((el) => {
            const elapsed = now - Number(el.dataset.startedAt);
            const eta = Number(el.dataset.etaS);
            // Past the slowest normal run: likely hung, worth a look.
            const slow = eta && elapsed > Number(el.dataset.slowS);
            el.classList.toggle("is-slow", Boolean(slow));
            if (!eta) el.textContent = clock(elapsed);
            else if (slow) el.textContent = `${clock(elapsed)} · may be stuck`;
            else if (elapsed <= eta) el.textContent = `${clock(elapsed)} / ~${clock(eta)}`;
            else el.textContent = `${clock(elapsed)} (est. ~${clock(eta)})`;
        });
    }

    function formatTimes(root) {
        root.querySelectorAll("[data-took-s]").forEach((el) => {
            el.textContent = clock(Number(el.dataset.tookS));
        });
        tickTimers();
        root.querySelectorAll("time[data-ts]").forEach((el) => {
            const date = new Date(Number(el.dataset.ts) * 1000);
            el.dateTime = date.toISOString();
            el.textContent = date.toLocaleString([], {
                day: "numeric",
                month: "short",
                hour: "2-digit",
                minute: "2-digit",
            });
        });
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

    function buildUploadingCard(videoCount) {
        const section = document.createElement("section");
        section.className = "clone-batch clone-batch-uploading";
        const label = document.createElement("p");
        label.className = "clone-batch-title";
        const bar = document.createElement("progress");
        bar.max = 100;
        bar.value = 0;
        section.append(label, bar);
        const noun = videoCount === 1 ? "video" : "videos";
        section.setProgress = (pct) => {
            bar.value = pct;
            label.textContent =
                pct < 100
                    ? `Uploading ${videoCount} ${noun}… ${pct}%`
                    : `Checking ${videoCount} ${noun} and queuing runs…`;
        };
        section.setProgress(0);
        return section;
    }

    function initSubmit(form) {
        const statusEl = form.querySelector(".generation-status");
        const submitBtn = form.querySelector('button[type="submit"]');
        const batches = document.getElementById("clone-batches");
        const empty = document.getElementById("clone-empty");
        let uploading = false;

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
            batches.prepend(pending);

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
                    empty.hidden = batches.children.length > 0;
                    setStatus(statusEl, errorMessage(xhr), true);
                    return;
                }
                const data = JSON.parse(xhr.responseText);
                const holder = document.createElement("div");
                holder.innerHTML = data.html;
                const batch = holder.firstElementChild;
                // live-gallery.js may have drawn this batch already (maybe
                // partial) while the request was still in flight.
                batches.querySelector(`[data-batch="${batch.dataset.batch}"]`)?.remove();
                pending.replaceWith(batch);
                formatTimes(batch);
                window.Generation.resumePendingCards();
                // The images usually stay for the next batch; the videos don't.
                window.FilePicker.clearPicker(
                    form.querySelector('.file-picker[data-field="videos"]')
                );
                const n = data.job_ids.length;
                setStatus(statusEl, `Queued ${n} run${n === 1 ? "" : "s"}.`, false);
            });
            xhr.addEventListener("error", () => {
                done();
                pending.remove();
                empty.hidden = batches.children.length > 0;
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
        form.querySelector('[name="export"]').value = params.export;
        form.querySelector('[name="mirror"]').checked = !!params.mirror;
        setStatus(form.querySelector(".generation-status"), "", false);
        form.scrollIntoView({ behavior: "smooth" });
    }

    function initPrefill(form) {
        const videos = JSON.parse(form.dataset.prefillVideos || "[]");
        if (!videos.length) return;
        window.FilePicker.setExisting("videos", videos);
    }

    function initRecreate(form) {
        document.getElementById("clone-batches").addEventListener("click", (e) => {
            const batchBtn = e.target.closest(".batch-recreate-btn");
            if (batchBtn) {
                fillForm(form, JSON.parse(batchBtn.closest(".clone-batch").dataset.params));
                return;
            }
            if (!e.target.closest(".recreate-btn")) return;
            const card = e.target.closest(".result-item[data-params]");
            if (!card) return;
            fillForm(form, JSON.parse(card.dataset.params));
        });
    }

    // "Upload to Drive": a batch's (or one card's) finished videos into the
    // account folder picked in the batch header, on the worker; each card
    // lists where its video went, polled until no upload is on its way.
    const DRIVE_POLL_MS = 3000;

    function driveStateItem(upload) {
        const li = document.createElement("li");
        li.dataset.status = upload.status;
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

    function initDriveUpload() {
        document.getElementById("clone-batches").addEventListener("click", (e) => {
            const btn = e.target.closest(".drive-upload-btn");
            if (!btn) return;
            const batch = btn.closest(".clone-batch");
            const select = batch.querySelector(".drive-target");
            const note = batch.querySelector(".drive-note");
            const cards = btn.dataset.scope === "card"
                ? [btn.closest(".result-item")]
                : [...batch.querySelectorAll(".result-item[data-job-id]")];
            const jobIds = cards
                .filter((c) => c.querySelector(".download-btn"))
                .map((c) => c.dataset.jobId);
            if (!select || !jobIds.length) return;
            btn.disabled = true;
            note.hidden = true;
            fetch("/clone/drive-upload", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ job_ids: jobIds, login_id: Number(select.value) }),
            })
                .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
                .then(({ ok, body }) => {
                    if (!ok) throw new Error(body.detail || "Upload refused");
                    showDriveStates(body.uploads);
                    pollDrive(jobIds);
                })
                .catch((err) => {
                    note.textContent = err.message;
                    note.hidden = false;
                })
                .finally(() => {
                    btn.disabled = false;
                });
        });
        // Uploads still on their way when the page opened.
        const pending = [...document.querySelectorAll('.drive-states li[data-status="queued"]')]
            .map((li) => li.closest(".result-item").dataset.jobId);
        if (pending.length) pollDrive([...new Set(pending)]);
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("clone-form");
        formatTimes(document);
        const batches = document.getElementById("clone-batches");
        batches.addEventListener("live-gallery:update", () => formatTimes(batches));
        initSubmit(form);
        initRecreate(form);
        initPrefill(form);
        initDriveUpload();
        setInterval(tickTimers, 1000);
    });
})();
