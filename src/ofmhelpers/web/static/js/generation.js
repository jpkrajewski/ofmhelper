/*
 * Shared "submit -> poll -> render inline" behaviour for the kie.ai-backed
 * generation tools (Seedance, Kling 3.0, Nano Banana Pro, Fake AI Model)
 * and the download tools. No page navigation, and non-blocking: every
 * submit immediately prepends its own "generating…" card to the
 * #results-panel gallery and polls `${prefix}/jobs/${job_id}/status` in the
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
(function () {
    // A single dropped request (a network blip, a momentary server hiccup)
    // shouldn't abandon a job that's still running fine on the server --
    // only give up after several consecutive failures.
    const MAX_CONSECUTIVE_FAILURES = 5;

    // Users unsure whether their click registered tend to click Generate
    // again right away -- this is a flat cooldown on the button itself, not
    // a "wait for this job to finish" lock (jobs still run in parallel and
    // resolve into the gallery independently); it just stops an impatient
    // double-click from firing the same job twice.
    const SUBMIT_COOLDOWN_MS = 3000;

    // Finished/failed job cards carry the same data-job-id/data-task/
    // data-params attributes the server-rendered gallery cards do, plus a
    // "↻ Recreate" button -- so the delegation in the page templates picks
    // up new cards automatically.
    function attachJobData(div, job) {
        div.dataset.jobId = job.job_id;
        div.dataset.task = job.task;
        div.dataset.params = JSON.stringify(job.params || {});
    }

    function buildRecreateButton() {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "recreate-btn";
        btn.textContent = "↻ Recreate";
        return btn;
    }

    function buildSourceLabel(label) {
        const source = document.createElement("p");
        source.className = "source";
        source.textContent = label;
        return source;
    }

    // An <audio> element shows nothing that identifies the file, so a voice
    // result was previously indistinguishable from every other voice result.
    // Same node the server-rendered cards carry (see
    // _generate_gallery_card.html / _asset_grid.html) so a fresh generation and
    // a reloaded one look identical.
    function buildFilenameLabel(name) {
        const el = document.createElement("p");
        el.className = "filename";
        el.textContent = name || "";
        el.title = name || "";
        return el;
    }

    function buildResultCard(kind, item, job, label) {
        const div = document.createElement("div");
        div.className = "result-item";
        attachJobData(div, job);

        const tag = kind === "video" ? "video" : kind === "audio" ? "audio" : "img";
        const media = document.createElement(tag);
        media.src = item.view_url;
        media.className = `result-${tag === "img" ? "image" : tag}`;
        if (tag === "video" || tag === "audio") media.controls = true;
        else media.alt = item.name;
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

        const dl = document.createElement("a");
        dl.href = item.download_url;
        dl.download = item.name;
        dl.dataset.localFallbackUrl = item.local_fallback_url || "";
        dl.className = "download-btn";
        dl.textContent = "⬇ Download";

        div.append(
            media,
            buildFilenameLabel(item.name),
            dl,
            buildRecreateButton(),
            buildSourceLabel(label)
        );
        return div;
    }

    function buildPendingCard(label) {
        const div = document.createElement("div");
        div.className = "result-item";
        div.innerHTML =
            '<div class="result-file"><span class="spinner"></span> generating…</div>';
        div.appendChild(buildSourceLabel(label));
        return div;
    }

    // kie.ai's hosted result can be ready well before the server's local
    // download finishes (see task_helpers.job_status_payload's "preview"
    // key) -- as soon as a poll response carries one, swap the spinner for
    // the actual media playing straight from that remote URL. When the job
    // finally reports "done", pollJob's normal card.replaceWith(...) swaps
    // this out for the locally-served version -- no extra code needed here
    // for that half of the swap.
    function showPreview(card, preview, resultKind) {
        if (card.dataset.previewUrl === preview.remote_url) return; // already showing it
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

        const placeholder = card.querySelector(".result-file");
        if (placeholder) placeholder.replaceWith(media);
        else card.prepend(media);
    }

    // job is optional: the "lost track of this job" fallback has no params
    // to restore, so that card gets no Recreate button.
    function buildFailedCard(label, error, job) {
        const div = document.createElement("div");
        div.className = "result-item";
        const box = document.createElement("div");
        box.className = "result-file";
        box.title = error;
        box.textContent = `⚠ ${error.length > 80 ? error.slice(0, 80) + "…" : error}`;
        div.appendChild(box);
        if (job) {
            attachJobData(div, job);
            div.appendChild(buildRecreateButton());
        }
        div.appendChild(buildSourceLabel(label));
        return div;
    }

    // Polls one job and swaps `card` for the outcome when it lands.
    // Standalone (no submit-scope state) so it works both for cards created
    // by a fresh submit AND for server-rendered still-running cards being
    // resumed after a page load.
    function pollJob(card, prefix, jobId, label, resultKind, interval = 2000, failures = 0) {
        fetch(`${prefix}/jobs/${jobId}/status`)
            .then(async (r) => {
                const job = await r.json();
                if (!r.ok) {
                    throw new Error(job.detail || `status check failed (${r.status})`);
                }
                return job;
            })
            .then((job) => {
                if (job.status === "running" || job.status === "queued") {
                    if (job.preview && job.preview.remote_url) {
                        showPreview(card, job.preview, resultKind);
                    }
                    setTimeout(
                        () =>
                            pollJob(card, prefix, jobId, label, resultKind,
                                Math.min(interval * 1.4, 8000), 0),
                        interval
                    );
                    return;
                }
                if (job.status === "failed") {
                    card.replaceWith(
                        buildFailedCard(label, job.error || "Generation failed.", job)
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
                if (job.failed_sources && job.failed_sources.length) {
                    const msg = job.failed_sources
                        .map((f) => `${f.source}: ${f.error}`)
                        .join("\n");
                    cards.push(buildFailedCard(label, msg, job));
                }
                if (!cards.length) {
                    cards.push(buildFailedCard(label, "No output produced.", job));
                }
                card.replaceWith(...cards);
            })
            .catch(() => {
                if (failures + 1 < MAX_CONSECUTIVE_FAILURES) {
                    setTimeout(
                        () => pollJob(card, prefix, jobId, label, resultKind, interval, failures + 1),
                        interval
                    );
                    return;
                }
                card.replaceWith(
                    buildFailedCard(
                        label,
                        `Lost track of this job's status -- check /action-log for job ${jobId}.`
                    )
                );
            });
    }

    function isJson(response) {
        return (response.headers.get("content-type") || "").includes("json");
    }

    function toolLabel(form) {
        if (form.dataset.toolLabel) return form.dataset.toolLabel;
        const select = document.getElementById("tool-select");
        if (select && select.selectedOptions.length) {
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

        // The card goes up BEFORE the request, not after it resolves: /run
        // uploads every reference file to kie.ai inside the request, so the
        // response can be ~10s away. Waiting for job_id to draw anything left
        // the user staring at an unchanged page long enough to assume the
        // click was lost (and click again). The card is real UI immediately;
        // polling just starts late, once we know what to poll for.
        const card = buildPendingCard(label);
        gallery.prepend(card);

        fetch(form.action, { method: "POST", body: formData })
            .then(async (r) => {
                // An expired session never reaches here -- session.js turns
                // that 401 into a redirect to /login. This is the residual
                // case: some other non-JSON response (a proxy error page, a
                // 502), which would otherwise surface as an opaque JSON parse
                // error that reads like the generator itself broke.
                if (r.redirected || !isJson(r)) {
                    throw new Error(
                        `Unexpected non-JSON response (${r.status}) -- try again.`
                    );
                }
                const data = await r.json();
                if (!r.ok) {
                    throw new Error(data.detail || "Request failed.");
                }
                pollJob(card, prefix, data.job_id, label, resultKind);
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
                    (card.querySelector(".source") || {}).textContent?.trim() || prefix;
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
                    // copy (same-origin, so plain navigation triggers a real
                    // download) if one was ever recorded for this asset.
                    const fallback = link.dataset.localFallbackUrl;
                    if (fallback) window.location.href = fallback;
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
