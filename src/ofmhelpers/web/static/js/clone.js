(() => {
    const DRIVE_POLL_MS = 3000;

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

    function validate(form, formData) {
        const maxVideos = Number(form.dataset.maxVideos);
        const maxImages = Number(form.dataset.maxImages);
        const videos = manifestCount(formData, "videos");
        const images = manifestCount(formData, "image");

        const keyField = form.querySelector('[name="api_key"]');
        if (keyField && !keyField.disabled && !String(formData.get("api_key") || "").trim()) return "Enter the API key.";
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
            empty.hidden =
                gallery.querySelector(".result-item") !== null ||
                document.querySelector(
                    ".ph-toolbar:not([hidden]) .hidden-toggle:not([hidden])"
                ) !== null;
        };

        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            if (uploading) return;

            if (window.FilePicker.restoring()) {
                setStatus(statusEl, "Wait: a video is downloading again.", true);
                return;
            }
            const invalid = validate(form, window.FilePicker.collectFormData(new FormData(form)));
            if (invalid) {
                setStatus(statusEl, invalid, true);
                return;
            }

            if (!(await window.ClonePicks.confirm(form))) return;
            const formData = window.FilePicker.collectFormData(new FormData(form));

            uploading = true;
            submitBtn.disabled = true;
            setStatus(statusEl, "");
            empty.hidden = true;
            const pending = buildUploadingCard(manifestCount(formData, "videos"));
            gallery.prepend(pending);

            let guarded = () => {};
            const done = () => {
                guarded();
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

                cards.forEach((card) => {
                    gallery.querySelector(`[data-job-id="${card.dataset.jobId}"]`)?.remove();
                });
                pending.replaceWith(...cards);
                window.Generation.resumePendingCards();

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
            guarded = window.UploadGuard.begin();
        });
    }

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

    function driveStateItem(upload) {
        const li = document.createElement("li");
        li.dataset.status = upload.status;
        li.dataset.login = upload.login_id;
        if (upload.status === "done") {
            li.append("In Drive: ");

            const remove = document.getElementById("card-drive-remove")
                ?.content.firstElementChild.cloneNode(true);
            if (!remove) {
                li.append(upload.folder_name);
                return li;
            }
            const a = document.createElement("a");
            a.href = upload.drive_url;
            a.target = "_blank";
            a.rel = "noopener";
            a.textContent = upload.folder_name;
            li.appendChild(a);
            remove.dataset.upload = upload.id;
            remove.setAttribute("aria-label", `Remove from ${upload.folder_name}`);
            li.append(" ", remove);
        } else if (upload.status === "failed") {
            const span = document.createElement("span");

            span.textContent = `Drive upload to ${upload.folder_name} failed${upload.error ? `: ${upload.error}` : ""}`;
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
            const shown = list.filter((u) => u.status !== "removed");
            if (states) states.replaceChildren(...shown.map(driveStateItem));
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

    const SOURCE_THUMB_PX = 400;

    function sourceOf(card) {
        let params = {};
        try {
            params = JSON.parse(card.dataset.params || "{}");
        } catch {
            return null;
        }
        const path = (params.reference_videos || [])[0];
        if (!path) return null;
        const file = String(path).split("/").pop();
        const cut = file.indexOf("__");
        if (cut <= 0 || file.length <= cut + 2) return null;
        return {
            name: file.slice(cut + 2),
            thumb: `/refs/thumb?path=${encodeURIComponent(path)}&size=${SOURCE_THUMB_PX}`,
        };
    }

    function showSource(card) {
        if (!card.matches(".result-item") || card.querySelector("[data-clone-source]")) return;
        const source = sourceOf(card);
        if (!source) return;
        const img = document.createElement("img");
        img.className = "result-video";
        img.dataset.cloneSource = "";
        img.alt = source.name;
        img.loading = "lazy";
        img.decoding = "async";
        img.addEventListener("error", () => {
            img.hidden = true;
        });
        img.src = source.thumb;
        card.prepend(img);
    }

    function decorateFinished(card) {
        if (!card.matches(".result-item") || card.querySelector(".drive-states")) return;
        const states = document.createElement("ul");
        states.className = "drive-states";
        card.querySelector(".result-actions")?.before(states);
        const sendTo = document.getElementById("card-send-to");
        if (sendTo && card.querySelector("video.result-video")) {
            card.querySelector(".result-actions")?.before(
                sendTo.content.firstElementChild.cloneNode(true)
            );
        }
        const item = document.getElementById("card-drive-item");
        const list = card.querySelector(".menu-list");
        if (!item || !list) return;

        const node = item.content.firstElementChild.cloneNode(true);
        list.insertBefore(node, list.querySelector(".hide-btn"));
    }

    function initSendTo(gallery) {
        gallery.addEventListener("click", async (e) => {
            const btn = e.target.closest(".send-to-edit, .send-to-spoof");
            const card = btn?.closest(".result-item[data-job-id]");
            if (!card) return;
            const buttons = card.querySelectorAll(".send-to-edit, .send-to-spoof");
            buttons.forEach((b) => {
                b.disabled = true;
            });
            const body = new URLSearchParams({
                target: btn.classList.contains("send-to-spoof") ? "spoof" : "edit",
            });
            if (card.dataset.fileIndex) body.set("index", card.dataset.fileIndex);
            try {
                const r = await fetch(`/clone/${encodeURIComponent(card.dataset.jobId)}/handoffs`, {
                    method: "POST",
                    body,
                });
                const answer = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(answer.detail || `failed (${r.status})`);
                window.location = answer.redirect;
            } catch (err) {
                buttons.forEach((b) => {
                    b.disabled = false;
                });
                window.alert(`Could not open it: ${err.message}`);
            }
        });
    }

    function initDriveUpload(gallery) {
        gallery.addEventListener("click", (e) => {
            const btn = e.target.closest(".drive-upload-btn");
            if (!btn) return;
            const jobId = btn.closest(".result-item")?.dataset.jobId;
            if (jobId) window.location.href = `/drive?upload=${encodeURIComponent(jobId)}`;
        });

        gallery.addEventListener("click", (e) => {
            const btn = e.target.closest(".drive-remove");
            if (!btn) return;
            const label = btn.getAttribute("aria-label");
            if (!window.confirm(`${label}?`)) return;
            const card = btn.closest(".result-item");
            btn.disabled = true;
            fetch(`/clone/drive-uploads/${btn.dataset.upload}/remove`, { method: "POST" })
                .then(async (r) => {
                    if (!r.ok) {
                        const body = await r.json().catch(() => ({}));
                        throw new Error(body.detail || `failed (${r.status})`);
                    }
                    pollDrive([card.dataset.jobId]);
                })
                .catch((err) => {
                    btn.disabled = false;
                    window.alert(`Could not remove it: ${err.message}`);
                });
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("clone-form");
        const gallery = document.getElementById("clone-gallery");
        gallery.addEventListener("gallery:card-ready", (e) => decorateFinished(e.target));
        gallery.addEventListener("gallery:card-failed", (e) => showSource(e.target));

        gallery.addEventListener("gallery:card-preview", (e) => {
            e.target.querySelector("[data-clone-source]")?.remove();
        });
        initSubmit(form, gallery);
        initRecreate(form, gallery);
        initPrefill(form);
        initDriveUpload(gallery);
        initSendTo(gallery);

        const pending = [...gallery.querySelectorAll('.drive-states li[data-status="queued"]')]
            .map((li) => li.closest(".result-item").dataset.jobId);
        if (pending.length) pollDrive([...new Set(pending)]);
    });
})();
