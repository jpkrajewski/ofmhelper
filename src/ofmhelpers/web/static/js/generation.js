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

    function buildResultCard(kind, item, job, label) {
        const div = document.createElement("div");
        div.className = "result-item";
        attachJobData(div, job);

        const media = document.createElement(kind === "video" ? "video" : "img");
        media.src = item.view_url;
        media.className = kind === "video" ? "result-video" : "result-image";
        if (kind === "video") media.controls = true;
        else media.alt = item.name;

        const dl = document.createElement("a");
        dl.href = item.download_url;
        dl.download = item.name;
        dl.className = "download-btn";
        dl.textContent = "⬇ Download";

        div.append(media, dl, buildRecreateButton(), buildSourceLabel(label));
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

    function toolLabel(form) {
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
        const panel = document.getElementById("results-panel");
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

        fetch(form.action, { method: "POST", body: formData })
            .then(async (r) => {
                const data = await r.json();
                if (!r.ok) {
                    throw new Error(data.detail || "Request failed.");
                }
                const card = buildPendingCard(label);
                gallery.prepend(card);
                pollJob(card, prefix, data.job_id, label, resultKind);
            })
            .catch((err) => {
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
    function resumePendingCards() {
        document.querySelectorAll(".result-item[data-pending]").forEach((card) => {
            const prefix = card.dataset.pollPrefix;
            const jobId = card.dataset.jobId;
            if (!prefix || !jobId) return;
            const label =
                (card.querySelector(".source") || {}).textContent?.trim() || prefix;
            pollJob(card, prefix, jobId, label, "image");
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
    });

    window.Generation = { submit };
})();
