(() => {

    const POLL_FIRST_MS = 2000;
    const POLL_MAX_MS = 8000;

    const SUBMIT_COOLDOWN_MS = 3000;

    function attachJobData(div, job) {
        div.dataset.jobId = job.job_id;
        div.dataset.task = job.task;
        div.dataset.params = JSON.stringify(job.params || {});
    }

    function cloneActions(id) {
        return document.getElementById(id).content.firstElementChild.cloneNode(true);
    }

    function buildDoneActions(item) {
        const row = cloneActions("card-actions-done");
        const dl = row.querySelector(".download-btn");
        dl.href = item.download_url;
        dl.download = item.download_name || item.name;
        dl.dataset.localFallbackUrl = item.local_fallback_url || "";
        return row;
    }

    function buildSourceLabel(label) {
        const source = document.createElement("p");
        source.className = "source";
        source.textContent = label;
        return source;
    }

    function buildFilenameLabel(name) {
        const el = document.createElement("p");
        el.className = "filename";
        el.textContent = name || "";
        return el;
    }

    function buildResultCard(kind, item, job, label) {
        const div = document.createElement("div");
        div.className = "result-item";
        attachJobData(div, job);
        div.dataset.fileIndex = item.index;

        if (item.removed && document.getElementById("card-actions-removed")) {
            div.append(buildRemovedMedia(item));
            if (item.removed_reason) div.append(buildRemovedReason(item));
            div.append(buildSourceLabel(label), cloneActions("card-actions-removed"));
            return div;
        }

        if (kind !== "video" && kind !== "audio" && kind !== "image") {
            const file = document.createElement("div");
            file.className = "result-file";
            file.textContent = item.name;
            div.append(file, buildSourceLabel(label), buildDoneActions(item));
            return div;
        }

        const tag = kind === "video" ? "video" : kind === "audio" ? "audio" : "img";
        const media = document.createElement(tag);

        media.src = item.preview_url || item.view_url;
        media.className = `result-${tag === "img" ? "image" : tag}`;
        if (tag === "video" || tag === "audio") media.controls = true;
        else {
            media.alt = item.name;
            media.loading = "lazy";
            media.decoding = "async";
        }
        if (tag === "video") {
            media.playsInline = true;
            media.preload = item.poster_url ? "none" : "metadata";
            if (item.poster_url) media.poster = item.poster_url;
        }

        if (item.local_fallback_url) {
            media.addEventListener(
                "error",
                function onErr() {
                    media.removeEventListener("error", onErr);
                    media.src = item.local_fallback_url;
                },
                { once: true }
            );
        }

        div.append(media);
        if (tag === "audio") div.append(buildFilenameLabel(item.name));
        div.append(
            buildSourceLabel(label),
            buildDoneActions(item)
        );
        return div;
    }

    function buildRemovedMedia(item) {
        const still = item.poster_url || item.preview_url;
        if (!still) {
            const file = document.createElement("div");
            file.className = "result-file";
            file.textContent = item.name;
            return file;
        }
        const img = document.createElement("img");
        img.src = still;
        img.className = "result-video";
        img.alt = item.name;
        img.loading = "lazy";
        return img;
    }

    function buildRemovedReason(item) {
        const note = document.createElement("div");
        note.className = "result-file result-failed fail-note";
        note.dataset.removedReason = "";
        note.title = item.removed_error || "";
        note.textContent = item.removed_reason;
        return note;
    }

    function buildPendingCard(label) {
        const div = document.createElement("div");
        div.className = "result-item";
        div.innerHTML =
            '<div class="result-file"><span class="spinner"></span> generating…</div>';
        div.appendChild(buildSourceLabel(label));
        return div;
    }

    function buildUploadingCard() {
        const div = document.createElement("div");
        div.className = "result-item";
        const box = document.createElement("div");
        box.className = "result-file";
        const bar = document.createElement("progress");
        bar.max = 100;
        bar.value = 0;
        box.append(bar);
        const line = buildSourceLabel("");
        div.append(box, line);
        div.setProgress = (pct) => {
            bar.value = pct;
            line.textContent = pct < 100 ? `Uploading… ${pct}%` : "Starting…";
        };
        div.setProgress(0);
        return div;
    }

    function parseJson(text) {
        try {
            return JSON.parse(text);
        } catch {
            return null;
        }
    }

    function showStage(card, media, stage) {
        let line = card.querySelector("[data-stage]");
        if (!stage) {
            line?.remove();
            return;
        }
        if (!line) {
            line = document.createElement("div");
            line.className = "result-file";
            line.dataset.stage = "";
            line.append(document.createElement("span"), document.createTextNode(""));
            line.firstChild.className = "spinner";
            media.after(line);
        }
        line.lastChild.textContent = ` ${stage}`;
    }

    function showPreview(card, preview, resultKind, stage) {
        if (card.dataset.previewUrl === preview.remote_url) {

            showStage(card, card.querySelector("[data-preview]"), stage);
            return;
        }
        card.dataset.previewUrl = preview.remote_url;

        const kind = preview.kind || resultKind;
        const tag = kind === "video" ? "video" : kind === "audio" ? "audio" : "img";
        const media = document.createElement(tag);
        media.src = preview.remote_url;
        media.className = `result-${tag === "img" ? "image" : tag}`;
        if (tag === "video" || tag === "audio") {
            media.controls = true;
            if (tag === "video") media.muted = true;
        } else {
            media.alt = "generating… (showing hosted preview)";
        }

        media.dataset.preview = "";
        const placeholder = card.querySelector(".result-file");
        if (placeholder) placeholder.replaceWith(media);
        else card.prepend(media);
        showStage(card, media, stage);

        card.dispatchEvent(new CustomEvent("gallery:card-preview", { bubbles: true }));
    }

    function keepMaker(from, cards) {
        const maker = from.querySelector("[data-made-by]");
        if (!maker) return;
        cards.forEach((card) => card.querySelector(".source")?.after(maker.cloneNode(true)));
    }

    function fillFailure(box, text) {
        const head = document.createElement("strong");
        head.className = "result-failed-head";
        head.textContent = text.head;
        box.append(head);
        if (text.said) {
            const said = document.createElement("span");
            said.className = "result-failed-said";
            said.textContent = text.said;
            box.append(said);
        }
        const keys = document.getElementById("card-failed-keys");
        if (text.about_keys && keys) box.append(keys.content.firstElementChild.cloneNode(true));
    }

    function buildFailedCard(label, error, job, line) {
        const div = document.createElement("div");
        div.className = "result-item";
        const box = document.createElement("div");
        box.className = "result-file result-failed";
        if (job?.error_reason) box.dataset.reason = job.error_reason;
        const text = job?.error_text;
        if (text) fillFailure(box, text);
        else box.textContent = line || error || "Failed";
        div.append(box, buildSourceLabel(label));
        if (job) {
            attachJobData(div, job);
            div.appendChild(cloneActions("card-actions-failed"));
        }
        return div;
    }

    function buildFailedSources(label, job) {
        const div = buildFailedCard(label, "", job, "");
        const box = div.querySelector(".result-failed");
        box.classList.add("fail-note");
        box.dataset.failedSources = "";
        box.textContent = "";
        for (const f of job.failed_sources) {
            const line = document.createElement("span");
            line.title = f.source;
            line.textContent = `${f.link} · ${f.reason}`;
            box.appendChild(line);
        }
        return div;
    }

    function onceShown(fn) {
        document.addEventListener("visibilitychange", function shown() {
            if (document.hidden) return;
            document.removeEventListener("visibilitychange", shown);
            fn();
        });
    }

    function pollJob(card, prefix, jobId, label, resultKind, interval = POLL_FIRST_MS) {

        if (document.hidden) {
            onceShown(() => pollJob(card, prefix, jobId, label, resultKind, interval));
            return;
        }
        function again(nextInterval) {
            setTimeout(
                () => pollJob(card, prefix, jobId, label, resultKind, nextInterval),
                interval
            );
        }
        fetch(`${prefix}/jobs/${jobId}/status`)
            .then((r) => {
                if (r.status === 404) return { status: "gone" };
                if (!r.ok || !isJson(r)) throw new Error(`status check failed (${r.status})`);
                return r.json();
            })
            .then((job) => {
                if (job.status === "running" || job.status === "queued") {
                    if (job.preview?.remote_url) {
                        showPreview(card, job.preview, resultKind, job.stage);
                    }
                    again(Math.min(interval * 1.4, POLL_MAX_MS));
                    return;
                }
                document.dispatchEvent(new CustomEvent("job:settled", { detail: { jobId } }));
                if (job.status === "gone") {
                    card.replaceWith(
                        buildFailedCard(label, `Job ${jobId} no longer exists.`, null, "Not found")
                    );
                    return;
                }
                if (job.status === "failed") {
                    const failed = buildFailedCard(
                        label, job.error || "Generation failed.", job, job.error_line
                    );
                    keepMaker(card, [failed]);
                    card.replaceWith(failed);

                    failed.dispatchEvent(
                        new CustomEvent("gallery:card-failed", { bubbles: true })
                    );
                    return;
                }

                const cards = job.result.map((item) =>
                    buildResultCard(item.kind || resultKind, item, job, label)
                );

                if (job.failed_sources?.length) {
                    cards.push(buildFailedSources(label, job));
                }
                if (!cards.length) {
                    cards.push(buildFailedCard(label, "No output produced.", job, "No output"));
                }
                keepMaker(card, cards);
                card.replaceWith(...cards);

                cards.forEach((c) => c.dispatchEvent(
                    new CustomEvent("gallery:card-ready", { bubbles: true })
                ));
            }, () => again(interval));
    }

    function isJson(response) {
        return (response.headers.get("content-type") || "").includes("json");
    }

    function toolLabel(form) {
        if (form.dataset.toolLabel) return form.dataset.toolLabel;
        const select = document.getElementById("model-select");
        if (select?.selectedOptions.length) {
            return select.selectedOptions[0].textContent.trim();
        }
        return form.dataset.prefix;
    }

    function submit(form, formData) {
        const prefix = form.dataset.prefix;
        const resultKind = form.dataset.resultKind;
        const label = toolLabel(form);
        const panel = document.getElementById(form.dataset.resultsTarget || "results-panel");
        const statusEl = panel.querySelector(".generation-status");
        const gallery = panel.querySelector(".results");
        const submitBtn = form.querySelector('button[type="submit"]');

        if (submitBtn) {
            submitBtn.disabled = true;
            setTimeout(() => {
                submitBtn.disabled = false;
            }, SUBMIT_COOLDOWN_MS);
        }

        function setSubmitError(message) {
            statusEl.innerHTML = '<div class="generation-error"></div>';
            statusEl.querySelector(".generation-error").textContent = message;
        }

        statusEl.innerHTML = "";

        const card = buildUploadingCard();
        gallery.prepend(card);

        const key = window.Idempotency.keyFor(form, formData);
        const headers = { [window.Idempotency.HEADER]: key };
        window.Idempotency.send(() =>
            window.UploadGuard.post(form.action, formData, card.setProgress, headers)
        )
            .then((xhr) => {

                if (xhr.status === 401) {
                    const loginUrl = parseJson(xhr.responseText)?.login_url;
                    if (loginUrl) {
                        window.UploadGuard.sessionGone();
                        window.SessionExpiry.expire(loginUrl);
                        return;
                    }
                }

                const data = parseJson(xhr.responseText);
                const type = xhr.getResponseHeader("content-type") || "";
                if (!type.includes("json") || data === null) {
                    throw new Error(
                        `Unexpected non-JSON response (${xhr.status}) -- try again.`
                    );
                }
                if (xhr.status < 200 || xhr.status >= 300) {
                    window.Idempotency.refused(form, key, xhr.status);
                    throw new Error(data.detail || "Request failed.");
                }

                function track(theCard, jobId) {
                    if (gallery.querySelector(`[data-job-id="${jobId}"]`)) {
                        theCard.remove();
                        return;
                    }
                    theCard.dataset.jobId = jobId;
                    pollJob(theCard, prefix, jobId, label, resultKind);
                }

                const ids = (data.job_ids || [data.job_id]).filter(Boolean);
                window.Idempotency.holdUntilSettled(form, key, ids);

                if (!ids.length) card.remove();
                else {
                    const first = buildPendingCard(label);
                    card.replaceWith(first);
                    track(first, ids[0]);
                }
                for (const id of ids.slice(1)) {
                    const extra = buildPendingCard(label);
                    gallery.prepend(extra);
                    track(extra, id);
                }
                form.dispatchEvent(new CustomEvent("generation:queued", { detail: data }));
            })
            .catch((err) => {
                card.replaceWith(buildFailedCard(label, err.message));
                setSubmitError(err.message);
            });
    }

    function initGenerationForm(form) {
        form.addEventListener("submit", (e) => {
            e.preventDefault();
            const formData = window.FilePicker
                ? window.FilePicker.collectFormData(new FormData(form))
                : new FormData(form);
            submit(form, formData);
        });
    }

    function resumePendingCards() {
        document
            .querySelectorAll(".result-item[data-pending]:not([data-resumed])")
            .forEach((card) => {
                const prefix = card.dataset.pollPrefix;
                const jobId = card.dataset.jobId;
                if (!prefix || !jobId) return;
                card.dataset.resumed = "1";
                const label =
                    card.querySelector(".source")?.textContent?.trim() || prefix;
                pollJob(card, prefix, jobId, label, card.dataset.pollKind || "image");
            });
    }

    function wireDownloadButtons() {
        document.addEventListener("click", (e) => {
            const link = e.target.closest("a.download-btn");
            if (!link) return;
            if (new URL(link.href, location.href).origin === location.origin) return;

            e.preventDefault();
            fetch(link.href)
                .then((r) => {
                    if (!r.ok) throw new Error("download failed");
                    return r.blob();
                })
                .then((blob) => {
                    const blobUrl = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = blobUrl;
                    a.download = link.download || "";
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    URL.revokeObjectURL(blobUrl);
                })
                .catch(() => {

                    const fallback = link.dataset.localFallbackUrl;
                    if (fallback) window.location.href = `${fallback}?dl=1`;
                    else window.open(link.href, "_blank", "noopener,noreferrer");
                });
        });
    }

    document.addEventListener(
        "error",
        (event) => {
            const media = event.target;
            const fallback = media.dataset?.localFallbackUrl;
            if (!fallback) return;
            delete media.dataset.localFallbackUrl;
            media.src = fallback;
        },
        true
    );

    document.addEventListener("DOMContentLoaded", () => {

        document
            .querySelectorAll("form[data-prefix][data-result-kind]:not([data-manual-submit])")
            .forEach(initGenerationForm);
        resumePendingCards();
        wireDownloadButtons();
    });

    window.Generation = { submit, resumePendingCards };
})();
