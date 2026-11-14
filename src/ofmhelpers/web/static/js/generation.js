/*
 * Shared "submit -> poll -> render inline" behaviour for /generate (every
 * AI provider, through /ai) and the download tools. No page navigation, and non-blocking: every
 * submit immediately prepends its own card to the #results-panel gallery
 * (upload progress, then "generating…" once the job exists; leaving the
 * page while it uploads asks first, upload-guard.js) and polls `${prefix}/jobs/${job_id}/status` in the
 * background, so more runs can be fired while earlier ones are still
 * working. When a job finishes, its card is swapped in place for the result
 * (or the error).
 *
 * Server-rendered gallery cards for jobs that are STILL RUNNING at page
 * load (marked data-pending + data-poll-prefix by the template) get a
 * poller attached on DOMContentLoaded too -- otherwise navigating away and
 * back would leave them spinning forever even after the job finishes.
 *
 * Any form with a `data-prefix` and `data-result-kind` ("video" or "image")
 * attribute is auto-wired on DOMContentLoaded via the ordinary submit event
 * (using FilePicker.collectFormData if that widget is present on the page).
 *
 * Pages with their own bespoke submit handling can add `data-manual-submit`
 * to opt out of the auto-wiring, and instead call
 * `Generation.submit(form, formData)` once they've built their own FormData.
 */
(() => {
    // A running job's polls back off from the first interval to the last.
    const POLL_FIRST_MS = 2000;
    const POLL_MAX_MS = 8000;

    // Users unsure whether their click registered tend to click Generate
    // again right away -- this is a flat cooldown on the button itself, not
    // a "wait for this job to finish" lock (jobs still run in parallel and
    // resolve into the gallery independently); it just stops an impatient
    // double-click from firing the same job twice.
    const SUBMIT_COOLDOWN_MS = 3000;

    // Finished/failed job cards carry the same data-job-id/data-task/
    // data-params attributes the server-rendered gallery cards do, plus the
    // same actions row -- so the delegation in the page templates picks up
    // new cards automatically.
    function attachJobData(div, job) {
        div.dataset.jobId = job.job_id;
        div.dataset.task = job.task;
        div.dataset.params = JSON.stringify(job.params || {});
    }

    // The card's one row of actions, cloned from the page's templates
    // (_card_actions.html, from the macros _gallery_card.html uses): a
    // result gets Download + the ⋯ menu (Recreate), a failure Recreate.
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

    // An <audio> element shows nothing that identifies the file, so an audio
    // result (only) carries its name. Same node the server-rendered cards
    // carry (_generate_gallery_card.html) so a fresh generation and a
    // reloaded one look identical.
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

        // A download whose video is not on the server (removed to save space
        // and not fetched again): what was kept of it and the Download that
        // fetches it, as _download_gallery_card.html draws it.
        if (item.removed && document.getElementById("card-actions-removed")) {
            div.append(buildRemovedMedia(item));
            if (item.removed_reason) div.append(buildRemovedReason(item));
            div.append(buildSourceLabel(label), cloneActions("card-actions-removed"));
            return div;
        }

        // Not media (a zip): no preview element, just the name -- same as
        // _asset_media.html's fallback, so it matches a reloaded card.
        if (kind !== "video" && kind !== "audio" && kind !== "image") {
            const file = document.createElement("div");
            file.className = "result-file";
            file.textContent = item.name;
            div.append(file, buildSourceLabel(label), buildDoneActions(item));
            return div;
        }

        const tag = kind === "video" ? "video" : kind === "audio" ? "audio" : "img";
        const media = document.createElement(tag);
        // Images and videos show their small preview, a video behind a
        // poster frame (same as _asset_media.html): a grid of 4K results must
        // not load every original.
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
        // item.local_fallback_url is only set when view_url points at kie.ai
        // and a local copy also exists -- swap to it once the hosted URL
        // goes stale (kie.ai's result URLs are only reliably valid ~24h)
        // instead of leaving a broken video/image.
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

    // Why a removed download did not come back, as
    // _download_gallery_card.html draws it under the poster.
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

    // The card while the form and its files go up: a progress bar, the
    // percentage on the card's line. It becomes the "generating…" card only
    // once the server answers with the job: until then there is no job, and
    // leaving the page drops the run (prod 2026-11-06: a big picture on a
    // slow uplink still uploading after 80 s showed "generating…", a refresh
    // and it was gone, nginx `400 0`, nothing ever reached the app).
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

    // kie.ai's hosted result can be ready well before the server's local
    // download finishes (see task_helpers.job_status_payload's "preview"
    // key) -- as soon as a poll response carries one, swap the spinner for
    // the actual media playing straight from that remote URL. When the job
    // finally reports "done", pollJob's normal card.replaceWith(...) swaps
    // this out for the locally-served version -- no extra code needed here
    // for that half of the swap. Under it, what the server is still making
    // of the result (`stage`: "Generated. Encoding: CapCut export…").
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
            // Already showing it: only its line may have changed.
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
        // The result is on the card: a page takes away what stood in for it
        // (clone.js: the first frame of the video being cloned).
        card.dispatchEvent(new CustomEvent("gallery:card-preview", { bubbles: true }));
    }

    // A card of someone else's run (a VA's, read by the owner) keeps its
    // "Done by VA: <name>" line when the run ends and the card is redrawn.
    function keepMaker(from, cards) {
        const maker = from.querySelector("[data-made-by]");
        if (!maker) return;
        cards.forEach((card) => card.querySelector(".source")?.after(maker.cloneNode(true)));
    }

    // job is optional: the "Not found" card (a job gone) has no params
    // to restore, so that card gets no Recreate button.
    // `line` is what the card shows (schemas/errors.py `error_line`, the
    // status payload's error_line): provider, reason and what it said, on
    // the card itself -- Safari shows no hover text.
    function buildFailedCard(label, error, job, line) {
        const div = document.createElement("div");
        div.className = "result-item";
        const box = document.createElement("div");
        box.className = "result-file result-failed";
        if (job?.error_reason) box.dataset.reason = job.error_reason;
        box.textContent = line || error || "Failed";
        div.append(box, buildSourceLabel(label));
        if (job) {
            attachJobData(div, job);
            div.appendChild(cloneActions("card-actions-failed"));
        }
        return div;
    }

    // Runs `fn` once the hidden tab is shown again.
    function onceShown(fn) {
        document.addEventListener("visibilitychange", function shown() {
            if (document.hidden) return;
            document.removeEventListener("visibilitychange", shown);
            fn();
        });
    }

    // Polls one job and swaps `card` for the outcome when it lands.
    // Standalone (no submit-scope state) so it works both for cards created
    // by a fresh submit AND for server-rendered still-running cards being
    // resumed after a page load.
    // Only the job's own answer ends the polling: a failed poll (offline,
    // a deploy's 502) is tried again for as long as it takes (prod
    // 2026-10-31: five failed polls on a phone put away turned a running
    // clone into "Lost track"; it finished fine 3 minutes later).
    function pollJob(card, prefix, jobId, label, resultKind, interval = POLL_FIRST_MS) {
        // A hidden tab (a phone put away, a laptop asleep) often has no
        // network, and nobody sees it: polled again the moment it is shown.
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
                    // For pages that add their own bits to a failed card
                    // (clone.js: the video it was cloning).
                    failed.dispatchEvent(
                        new CustomEvent("gallery:card-failed", { bubbles: true })
                    );
                    return;
                }
                // item.kind (from the server, by file extension) is the
                // source of truth -- Fake AI Model can produce either kind
                // per run, the rest always match resultKind anyway.
                const cards = job.result.map((item) =>
                    buildResultCard(item.kind || resultKind, item, job, label)
                );
                // Grouped download jobs can finish "done" with some sources
                // failed -- surface those as their own card so partial
                // failures don't vanish silently.
                if (job.failed_sources?.length) {
                    const msg = job.failed_sources
                        .map((f) => `${f.source}: ${f.error}`)
                        .join("\n");
                    const n = job.failed_sources.length;
                    cards.push(buildFailedCard(label, msg, job, `${n} link${n === 1 ? "" : "s"} failed`));
                }
                if (!cards.length) {
                    cards.push(buildFailedCard(label, "No output produced.", job, "No output"));
                }
                keepMaker(card, cards);
                card.replaceWith(...cards);
                // For pages that add their own bits to a finished card
                // (clone.js: Drive).
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

        // The card goes up BEFORE the request, not after it resolves: the
        // files upload inside the request, which on a slow line takes
        // minutes. Waiting for job_id to draw anything left the user staring
        // at an unchanged page long enough to assume the click was lost (and
        // click again). It shows the upload, then "generating…" once the job
        // exists; polling starts then, once we know what to poll for.
        const card = buildUploadingCard();
        gallery.prepend(card);

        window.UploadGuard.post(form.action, formData, card.setProgress)
            .then((xhr) => {
                // session.js wraps fetch only: the same takeover (its
                // overlay, one redirect however many requests 401) here.
                if (xhr.status === 401) {
                    const loginUrl = parseJson(xhr.responseText)?.login_url;
                    if (loginUrl) {
                        window.UploadGuard.sessionGone();
                        window.SessionExpiry.expire(loginUrl);
                        return;
                    }
                }
                // Some other non-JSON response (a proxy error page, a 502,
                // a redirect followed to an HTML page) would otherwise
                // surface as an opaque JSON parse error that reads like the
                // generator itself broke.
                const data = parseJson(xhr.responseText);
                const type = xhr.getResponseHeader("content-type") || "";
                if (!type.includes("json") || data === null) {
                    throw new Error(
                        `Unexpected non-JSON response (${xhr.status}) -- try again.`
                    );
                }
                if (xhr.status < 200 || xhr.status >= 300) {
                    throw new Error(data.detail || "Request failed.");
                }

                // Start polling `theCard` for `jobId`, unless live-gallery.js
                // already drew that job from the server while the request was
                // in flight (then the optimistic card is a duplicate).
                function track(theCard, jobId) {
                    if (gallery.querySelector(`[data-job-id="${jobId}"]`)) {
                        theCard.remove();
                        return;
                    }
                    theCard.dataset.jobId = jobId;
                    pollJob(theCard, prefix, jobId, label, resultKind);
                }

                // /ai/run answers with one id per image (a batch); other
                // tools answer with a single job_id.
                const ids = data.job_ids || [data.job_id];
                // Nothing new to run (/download-media: every link was
                // downloaded before): no card of its own; the page shows the
                // ones there are (download.js reads `detail.again`).
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
                // The optimistic card can't become a result now -- turn it
                // into the error so a failed submit never leaves a card
                // spinning forever.
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

    // Server-rendered cards for jobs still running at page load: resume
    // polling so they resolve inline instead of spinning forever.
    // Idempotent, because it is also called on cards appended after load (see
    // gallery-scroll.js): data-resumed marks the ones already being polled, so
    // a second call can't start a duplicate poller for the same job.
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

    // Result assets are prioritised to kie.ai's own hosted URL (faster than
    // proxying through our server, and it stays valid for kie.ai's 14-day
    // retention window). But browsers ignore the `download` attribute on a
    // cross-origin link -- it just opens/plays the file instead of saving it.
    // Fetch it client-side (browser -> kie.ai directly, never through our
    // server) and save the blob instead; same-origin links (the local
    // `/files/...` fallback) keep working via plain navigation.
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
                    // kie.ai's URL has gone stale -- fall back to our own
                    // copy if one was ever recorded for this asset. `dl=1`
                    // makes the server send it as an attachment: the bare
                    // URL is the inline view, it only showed the picture.
                    const fallback = link.dataset.localFallbackUrl;
                    if (fallback) window.location.href = `${fallback}?dl=1`;
                    else window.open(link.href, "_blank");
                });
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        // data-manual-submit opts a form out of auto-wiring -- for pages that
        // need to transform the FormData themselves before handing off to
        // Generation.submit.
        document
            .querySelectorAll("form[data-prefix][data-result-kind]:not([data-manual-submit])")
            .forEach(initGenerationForm);
        resumePendingCards();
        wireDownloadButtons();
    });

    // resumePendingCards is exported for pages that add cards after load
    // (gallery-scroll.js). wireDownloadButtons deliberately isn't: it is one
    // delegated document listener, so appended cards are already covered and a
    // second call would only double-bind it.
    window.Generation = { submit, resumePendingCards };
})();
