(() => {
    const REVIEW_POLL_MS = 4000;

    const REVIEW_CHUNK = 100;

    const RESORT_GUARD_MS = 400;
    const NOT_SURE_SENT = "Not sure it was sent — check the cards below before sending again";
    const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
    const credits = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

    function setStatus(statusEl, message, isError) {
        document.getElementById("clone-links-stop").hidden = true;
        statusEl.innerHTML = "";
        if (!message) return;
        const el = document.createElement("div");
        if (isError) el.className = "generation-error";
        el.textContent = message;
        statusEl.appendChild(el);
    }

    function xhrError(xhr) {
        try {
            const data = JSON.parse(xhr.responseText);
            if (typeof data.detail === "string") return data.detail;
            if (data.detail && typeof data.detail.message === "string") return data.detail.message;
        } catch {

        }
        return `Request failed (${xhr.status}) — try again.`;
    }

    async function refusalOf(response) {
        const body = await response.json().catch(() => ({}));
        if (body.detail && typeof body.detail.message === "string") return body.detail;
        if (typeof body.detail === "string") return { message: body.detail, lines: [] };
        return { message: `Check failed (${response.status}) — try again.`, lines: [] };
    }

    function refusedLine(line) {
        const li = document.createElement("li");
        li.className = "cost-line cost-line-flagged";
        const label = document.createElement("strong");
        label.className = "cost-line-name";
        label.textContent = line.text;
        label.title = line.text;
        const why = document.createElement("span");
        why.className = "cost-line-detail";
        why.textContent = line.reason;
        li.append(label, why);
        return li;
    }

    function showRefused(refusal) {
        const dialog = document.getElementById("clone-links-refused");
        const list = document.getElementById("clone-links-refused-lines");
        document.getElementById("clone-links-refused-title").textContent = refusal.message;
        list.replaceChildren(...(refusal.lines || []).map(refusedLine));
        list.hidden = list.children.length === 0;

        document.getElementById("clone-links-refused-note").hidden = list.hidden;
        dialog.showModal();
    }

    function badLinesFirst(box, numbers) {
        const lines = box.value.split(/\r\n|\r|\n/);
        const bad = new Set(numbers.map((n) => n - 1));
        const top = lines.filter((_, i) => bad.has(i));
        const rest = lines.filter((line, i) => !bad.has(i) && line.trim());
        box.value = [...top, ...rest].join("\n");
        box.scrollTop = 0;
        box.scrollIntoView({ block: "center" });
        return top.join("\n").length;
    }

    async function readLinks(box) {
        if (!box.value.trim()) {
            showRefused({ message: "Paste the links first", lines: [] });
            return null;
        }
        const body = new FormData();
        body.append("links", box.value);
        try {
            const r = await fetch("/clone-links/check", { method: "POST", body });
            if (!r.ok) {
                const refusal = await refusalOf(r);
                const numbers = (refusal.lines || []).map((line) => line.number);
                const selected = numbers.length ? badLinesFirst(box, numbers) : 0;
                showRefused(refusal);
                if (selected) {
                    document.getElementById("clone-links-refused").addEventListener("close", () => {
                        box.focus({ preventScroll: true });
                        box.setSelectionRange(0, selected);
                        box.scrollTop = 0;
                    }, { once: true });
                }
                return null;
            }
            const found = await r.json();
            box.value = found.links.join("\n");
            return found.links;
        } catch {
            showRefused({ message: "No connection — try again", lines: [] });
            return null;
        }
    }

    function initCheck(form) {
        const box = form.querySelector('[name="links"]');
        const button = form.querySelector(".check-links-btn");
        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                await readLinks(box);
            } finally {
                button.disabled = false;
            }
        });
    }

    async function streamPreviews(urls, provider, resolution, signal, onPreview) {
        const answered = new Set();
        const take = (preview) => {
            answered.add(preview.url);
            onPreview(preview);
        };
        try {
            const r = await fetch("/clone-links/previews", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ urls, provider, resolution }),
                signal,
            });
            if (!r.ok || !r.body) throw new Error(`previews ${r.status}`);
            const reader = r.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            for (;;) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop();
                lines.filter((line) => line.trim()).forEach((line) => take(JSON.parse(line)));
            }
        } catch {
            if (signal.aborted) return;
        }
        urls.filter((url) => !answered.has(url))
            .forEach((url) => take({ url, error: "No connection — try again" }));
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function pickTile(url) {
        const li = el("li", "pick-tile");
        li.dataset.loading = "";
        const cover = el("a", "pick-cover");
        cover.href = url;
        cover.target = "_blank";
        cover.rel = "noopener";
        cover.title = url;
        cover.append(el("span", "spinner"));
        const meta = el("span", "pick-meta");
        meta.append(el("span", "pick-len", ""), el("span", "pick-price", ""));
        const remove = document.getElementById("pick-remove").content.firstElementChild.cloneNode(true);
        li.append(cover, meta, remove);
        li.url = url;
        return li;
    }

    function fillTile(li, preview) {
        delete li.dataset.loading;
        li.preview = preview;
        const cover = li.querySelector(".pick-cover");
        cover.replaceChildren();
        if (preview.thumb_url) {
            const img = document.createElement("img");
            img.src = preview.thumb_url;
            img.alt = "";
            cover.append(img);
        }
        const length = preview.seconds == null ? "" : `${preview.seconds.toFixed(1)} s`;
        li.querySelector(".pick-len").textContent = length;
        li.querySelector(".pick-price").textContent = preview.ready ? usd.format(preview.usd) : "";
        if (preview.ready) return;
        li.dataset.problem = "";
        const why = el("span", "pick-why",
            preview.error || `Too long (max ${preview.max_s} s)`);
        why.title = why.textContent;
        li.querySelector(".pick-meta").after(why);
    }

    function showDriveRoom(n) {
        const notice = document.getElementById("clone-links-confirm-space");
        if (!notice) return;
        notice.hidden = !(n * Number(notice.dataset.reelMb) > Number(notice.dataset.freeMb));
    }

    function pick(urls, provider, resolution) {
        const dialog = document.getElementById("clone-links-confirm");
        const list = document.getElementById("clone-links-confirm-lines");
        const go = document.getElementById("clone-links-confirm-go");

        dialog.style.setProperty("--pick-cols", String(Math.min(urls.length, 6)));
        const fresh = urls.map(pickTile);
        const byUrl = new Map(fresh.map((li) => [li.url, li]));
        list.replaceChildren(...fresh);
        const tiles = () => [...list.children];
        const ready = () => tiles().filter((li) => li.preview?.ready);

        let ended = false;
        let sortedAt = Number.NEGATIVE_INFINITY;

        function update() {
            const runs = ready();
            const n = runs.length;
            const loading = !ended && tiles().some((li) => !li.preview);
            const total = runs.reduce((sum, li) => sum + li.preview.usd, 0);
            const totalCredits = runs.reduce((sum, li) => sum + li.preview.credits, 0);
            const seconds = runs.reduce((sum, li) => sum + li.preview.seconds, 0);
            const summary = n
                ? `${n} video${n === 1 ? "" : "s"} · ${seconds.toFixed(1)} s · ${resolution} · ${credits.format(totalCredits)} credits`
                : loading ? "" : "None of these can be cloned";
            document.getElementById("clone-links-confirm-title").textContent =
                n ? `Generate ${n} clone${n === 1 ? "" : "s"}?` : "Generate clones?";
            document.getElementById("clone-links-confirm-summary").textContent = summary;
            document.getElementById("clone-links-confirm-usd").textContent = n ? usd.format(total) : "";
            go.textContent = n ? `Generate ${n} · ${usd.format(total)}` : "Generate";
            go.disabled = n === 0 || loading;
            showDriveRoom(n);
        }

        function sort() {
            const rank = (li) => (!li.preview ? 1 : li.preview.ready ? 0 : 2);
            const dearer = (a, b) =>
                b.preview.usd - a.preview.usd || b.preview.seconds - a.preview.seconds;
            const before = tiles();
            const sorted = [...before].sort((a, b) =>
                rank(a) - rank(b) || (rank(a) === 0 ? dearer(a, b) : 0));
            if (sorted.every((li, i) => li === before[i])) return false;
            list.append(...sorted);
            return true;
        }

        list.onclick = (e) => {
            const remove = e.target.closest(".pick-remove");
            if (!remove) return;
            if (performance.now() - sortedAt < RESORT_GUARD_MS) return;
            remove.closest(".pick-tile").remove();
            update();
        };
        const asking = new AbortController();
        streamPreviews(urls, provider, resolution, asking.signal, (preview) => {
            const li = byUrl.get(preview.url);
            if (!li) return;
            fillTile(li, preview);
            update();
        }).then(() => {
            if (asking.signal.aborted) return;
            ended = true;
            if (sort()) sortedAt = performance.now();
            update();
        });
        update();
        dialog.returnValue = "";
        dialog.showModal();
        return new Promise((resolve) => {
            dialog.addEventListener("close", () => {
                asking.abort();
                resolve(dialog.returnValue === "confirm" ? ready().map((li) => li.url) : null);
            }, { once: true });
        });
    }

    function validate(form, formData) {
        if (form.dataset.driveReady !== "true") return "Connect Google Drive on your Account page first";
        const maxImages = Number(form.dataset.maxImages);
        let images = 0;
        try {
            images = JSON.parse(formData.get("image_manifest") || "[]").length;
        } catch {
            images = 0;
        }

        const keyField = form.querySelector('[name="api_key"]');
        if (keyField && !keyField.disabled && !String(formData.get("api_key") || "").trim()) return "Enter the API key.";
        if (images < 1) return "Pick at least one subject image.";
        if (images > maxImages) return `Pick at most ${maxImages} subject images.`;
        if (!String(formData.get("prompt") || "").trim()) return "Enter a prompt.";
        return null;
    }

    function buildUploadingCard(runs) {
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
        const noun = runs === 1 ? "run" : "runs";
        card.setProgress = (pct) => {
            bar.value = pct;
            label.textContent = pct < 100 ? `Sending… ${pct}%` : `Queuing ${runs} ${noun}…`;
        };
        card.setProgress(0);
        return card;
    }

    function newBatch() {
        const bytes = crypto.getRandomValues(new Uint8Array(8));
        return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
    }

    function showStop(jobId) {
        const stop = document.getElementById("clone-links-stop");
        stop.dataset.job = jobId;
        stop.hidden = false;
    }

    function initStop(form) {
        const stop = document.getElementById("clone-links-stop");
        const statusEl = form.querySelector(".generation-status");
        stop.addEventListener("click", async () => {
            stop.disabled = true;
            try {
                const r = await fetch(`/clone-links/stop/${encodeURIComponent(stop.dataset.job)}`, {
                    method: "POST",
                });
                if (!r.ok) throw new Error(`stop ${r.status}`);
                setStatus(statusEl, "Stopped: runs not started yet won't run.", false);
            } catch {
                setStatus(statusEl, "Not stopped — press Stop again.", true);
                stop.hidden = false;
            } finally {
                stop.disabled = false;
            }
        });
    }

    function initGenerate(form, gallery) {
        const statusEl = form.querySelector(".generation-status");
        const submitBtn = form.querySelector('button[type="submit"]');
        const box = form.querySelector('[name="links"]');
        const empty = document.getElementById("clone-links-empty");
        let busy = false;

        box.addEventListener("input", () => {
            delete box.dataset.batch;
        });

        let guarded = () => {};
        const idle = () => {
            guarded();
            busy = false;
            submitBtn.disabled = false;
        };

        function unsure(pending) {
            pending.remove();
            empty.hidden = gallery.querySelector(".result-item") !== null;
            setStatus(statusEl, NOT_SURE_SENT, true);
            gallery.dispatchEvent(new CustomEvent("live-gallery:refresh"));
        }

        function send(formData, runs, sentText) {
            const pending = buildUploadingCard(runs);
            empty.hidden = true;
            gallery.prepend(pending);
            const xhr = new XMLHttpRequest();
            xhr.open("POST", form.action);
            xhr.upload.addEventListener("progress", (ev) => {
                if (ev.lengthComputable) pending.setProgress(Math.floor((ev.loaded / ev.total) * 100));
            });
            xhr.addEventListener("load", () => {
                idle();
                if (xhr.status === 401) {
                    const login = JSON.parse(xhr.responseText || "{}").login_url;
                    window.location.href = login || "/login";
                    return;
                }
                if (xhr.status >= 500) return unsure(pending);
                if (xhr.status < 200 || xhr.status >= 300) {

                    pending.remove();
                    empty.hidden = gallery.querySelector(".result-item") !== null;
                    setStatus(statusEl, xhrError(xhr), true);
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

                if (box.value === sentText) box.value = "";
                delete box.dataset.batch;
                const n = data.job_ids.length;
                setStatus(statusEl, `Queued ${n} run${n === 1 ? "" : "s"}.`, false);
                if (n) showStop(data.job_ids[0]);
            });
            xhr.addEventListener("error", () => {
                idle();
                unsure(pending);
            });
            xhr.send(formData);
            guarded = window.UploadGuard.begin();
        }

        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            if (busy) return;
            const formData = window.FilePicker.collectFormData(new FormData(form));
            formData.delete("links");
            const invalid = validate(form, formData);
            if (invalid) {
                setStatus(statusEl, invalid, true);
                return;
            }
            busy = true;
            submitBtn.disabled = true;
            setStatus(statusEl, "");
            const urls = await readLinks(box);
            if (!urls) return idle();
            const provider = String(formData.get("provider") || "");
            const resolution = String(formData.get("resolution") || "");
            const picked = await pick(urls, provider, resolution);
            if (!picked || !picked.length) return idle();
            picked.forEach((url) => formData.append("links", url));
            box.dataset.batch ||= newBatch();
            formData.append("batch", box.dataset.batch);
            send(formData, picked.length, box.value);
        });
    }

    function fillForm(form, params, links) {
        window.FilePicker.setExisting("image", params.reference_images || []);
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
        if (params.export) form.querySelector('[name="export"]').value = params.export;
        form.querySelector('[name="mirror"]').checked = !!params.mirror;
        const box = form.querySelector('[name="links"]');
        box.value = links.join("\n");
        box.dispatchEvent(new Event("input", { bubbles: true }));
        setStatus(form.querySelector(".generation-status"), "", false);
        form.scrollIntoView({ behavior: "smooth" });
    }

    async function recreateLinks(jobId, link) {
        try {
            const r = await fetch(`/clone-links/recreate/${encodeURIComponent(jobId)}`);
            if (r.ok) return (await r.json()).links;
        } catch {

        }
        return link ? [link] : [];
    }

    function initRecreate(form, gallery) {
        gallery.addEventListener("click", async (e) => {
            if (!e.target.closest(".recreate-btn")) return;
            const card = e.target.closest(".result-item[data-params]");
            if (!card) return;
            const params = JSON.parse(card.dataset.params);
            const links = await recreateLinks(card.dataset.jobId, params.link);
            if (links.length) return fillForm(form, params, links);

            setStatus(form.querySelector(".generation-status"), "Nothing to recreate.", false);
            form.scrollIntoView({ behavior: "smooth" });
        });
    }

    function setWhy(btn, open) {
        btn.nextElementSibling.hidden = !open;
        btn.setAttribute("aria-expanded", String(open));
        const icon = document.getElementById(open ? "icon-eye-off" : "icon-eye");
        btn.querySelector("svg")?.replaceWith(icon.content.firstElementChild.cloneNode(true));
    }

    function replaceReview(state) {
        const old = document.querySelector(`.ai-check[data-job="${state.job_id}"]`);
        if (!old) return;
        const wasOpen = old.querySelector(".ai-check-why")?.hidden === false;
        const holder = document.createElement("div");
        holder.innerHTML = state.html;
        const fresh = [...holder.children];
        old.replaceWith(...fresh);
        const btn = fresh[0]?.querySelector(".why-toggle");
        if (wasOpen && btn?.nextElementSibling?.matches(".ai-check-why")) setWhy(btn, true);
    }

    function pollChunk(ids) {
        const query = ids.map((id) => `job=${encodeURIComponent(id)}`).join("&");
        return fetch(`/clone-links/reviews?${query}`)
            .then((r) => {
                if (!r.ok) throw new Error(`reviews ${r.status}`);
                return r.json();
            })
            .then((states) => states.forEach(replaceReview))
            .catch((err) => console.warn("clone-links review poll failed", err));
    }

    function pollReviews(gallery) {
        const open = [...gallery.querySelectorAll(".ai-check[data-job]:not([data-settled])")];
        const next = () => setTimeout(() => pollReviews(gallery), REVIEW_POLL_MS);
        if (!open.length || document.hidden) return next();
        const ids = [...new Set(open.map((block) => block.dataset.job))];
        const chunks = [];
        for (let i = 0; i < ids.length; i += REVIEW_CHUNK) chunks.push(ids.slice(i, i + REVIEW_CHUNK));
        Promise.all(chunks.map(pollChunk)).finally(next);
    }

    function decorateFinished(card) {
        if (!card.matches(".result-item") || !card.querySelector(".download-btn")) return;
        if (card.querySelector(".ai-check")) return;
        const block = document.getElementById("card-review").content.firstElementChild.cloneNode(true);
        block.dataset.job = card.dataset.jobId;
        card.querySelector(".result-actions")?.before(block);
    }

    function initWhy(gallery) {
        gallery.addEventListener("click", (e) => {
            const btn = e.target.closest(".why-toggle");
            const panel = btn?.nextElementSibling;
            if (!panel?.matches(".ai-check-why")) return;
            setWhy(btn, panel.hidden);
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("clone-links-form");
        const gallery = document.getElementById("clone-links-gallery");
        gallery.addEventListener("gallery:card-ready", (e) => decorateFinished(e.target));
        initCheck(form);
        initGenerate(form, gallery);
        initStop(form);
        initRecreate(form, gallery);
        initWhy(gallery);
        pollReviews(gallery);
    });
})();
