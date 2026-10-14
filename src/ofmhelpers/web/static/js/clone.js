/*
 * /clone page controller.
 *
 * Submit: one POST queues a Wan 3.0 run per video and answers with the new
 * batch already rendered (the same _clone_batch.html partial the page uses),
 * so it is inserted in place -- no reload -- and generation.js polls its
 * cards like any other. XHR rather than fetch only for upload progress:
 * several videos can take a while to send.
 *
 * Recreate: on a batch header it restores the image, every video and the
 * settings; on a single result, the image and just that video. Files come
 * back as "existing" picker entries, so nothing is uploaded again.
 */
(function () {
    function setStatus(statusEl, message, isError) {
        statusEl.innerHTML = "";
        if (!message) return;
        const el = document.createElement("div");
        if (isError) el.className = "generation-error";
        el.textContent = message;
        statusEl.appendChild(el);
    }

    function formatTimes(root) {
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
        const videos = manifestCount(formData, "videos");
        if (!String(formData.get("api_key") || "").trim()) return "Enter the kie.ai API key.";
        if (manifestCount(formData, "image") !== 1) return "Pick one subject image.";
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
                pending.replaceWith(batch);
                formatTimes(batch);
                window.Generation.resumePendingCards();
                // The image usually stays for the next batch; the videos don't.
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

    function fillForm(form, params, message) {
        window.FilePicker.setExisting("image", params.reference_images || []);
        window.FilePicker.setExisting("videos", params.reference_videos || []);
        const prompt = form.querySelector('[name="prompt"]');
        prompt.value = params.prompt || "";
        prompt.dispatchEvent(new Event("input", { bubbles: true }));
        if (params.resolution) form.querySelector('[name="resolution"]').value = params.resolution;
        form.querySelector('[name="audio"]').checked = !!params.audio;
        setStatus(form.querySelector(".generation-status"), message, false);
        form.scrollIntoView({ behavior: "smooth" });
    }

    function initRecreate(form) {
        document.getElementById("clone-batches").addEventListener("click", (e) => {
            const batchBtn = e.target.closest(".batch-recreate-btn");
            if (batchBtn) {
                const params = JSON.parse(batchBtn.closest(".clone-batch").dataset.params);
                const n = params.reference_videos.length;
                fillForm(form, params, `Loaded batch: ${n} video${n === 1 ? "" : "s"}. Press Start to run it again.`);
                return;
            }
            if (!e.target.closest(".recreate-btn")) return;
            const card = e.target.closest(".result-item[data-params]");
            if (!card) return;
            fillForm(form, JSON.parse(card.dataset.params), "Loaded 1 video. Press Start to run it again.");
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("clone-form");
        formatTimes(document);
        initSubmit(form);
        initRecreate(form);
    });
})();
