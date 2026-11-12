/*
 * /download-assets: hand a finished download's video to /clone or
 * /clone-long. Each file is its own card; its [Clone | Clone long] posts the
 * job id and the card's file index, and the server answers the URL to open
 * with that video already picked.
 *
 * A reloaded card carries the control from the server
 * (_download_gallery_card.html); a card that finishes while the page is open
 * has it cloned in on gallery:card-ready -- its video is what decides it,
 * the same test the server makes.
 *
 * A video is kept on the server for a while, then only its poster is
 * (features/downloads/cleanup.py). Such a card has one Download: it asks
 * for the video again (POST /download-media/{job}/replays) and the card is
 * polled like a new download until the video is back. A link pasted again
 * downloads nothing twice: the answer names the jobs that hold it
 * (`again`), and their cards go to the first place.
 */
(() => {
    const PREFIX = "/download-media";
    // Pages of the gallery read to find the cards of jobs asked for again.
    const PAGES = 5;

    function targetOf(btn) {
        return btn.classList.contains("send-to-long") ? "clone-long" : "clone";
    }

    async function send(btn) {
        const card = btn.closest(".result-item[data-job-id]");
        if (!card) return;
        const buttons = card.querySelectorAll(".send-to-clone, .send-to-long");
        buttons.forEach((b) => {
            b.disabled = true;
        });
        try {
            const body = new URLSearchParams({
                job_id: card.dataset.jobId,
                target: targetOf(btn),
                index: card.dataset.fileIndex,
            });
            const r = await fetch("/download-media/send", { method: "POST", body });
            if (!r.ok) throw new Error((await r.text()) || r.status);
            const { redirect } = await r.json();
            window.location = redirect;
        } catch (err) {
            buttons.forEach((b) => {
                b.disabled = false;
            });
            console.error("send to clone failed", err);
        }
    }

    // A live-finished video download: give it the same control reloaded cards
    // already have.
    function decorate(card) {
        if (card.dataset.task !== "download_media") return;
        if (card.querySelector(".send-to")) return;
        if (!card.querySelector("video.result-video")) return;
        const template = document.getElementById("card-send-to");
        const meta = card.querySelector(".source");
        if (!template || !meta) return;
        const node = template.content.firstElementChild.cloneNode(true);
        meta.insertAdjacentElement("afterend", node);
    }

    function cardsOf(gallery, jobId) {
        return [...gallery.querySelectorAll(`.result-item[data-job-id="${jobId}"]`)];
    }

    // Every card of the job becomes one "downloading…" card, polled by
    // generation.js like a download just started: it draws the files again
    // when the job is done.
    function showFetching(gallery, jobId) {
        const [first, ...rest] = cardsOf(gallery, jobId);
        if (!first) return;
        rest.forEach((card) => card.remove());
        const label = first.querySelector(".source")?.textContent?.trim() || "Download";
        const box = document.createElement("div");
        box.className = "result-file";
        const spinner = document.createElement("span");
        spinner.className = "spinner";
        box.append(spinner, " downloading…");
        const source = document.createElement("p");
        source.className = "source";
        source.textContent = label;
        first.replaceChildren(box, source);
        first.dataset.pending = "";
        first.dataset.pollPrefix = PREFIX;
        first.dataset.pollKind = "video";
        first.removeAttribute("data-resumed");
        window.Generation.resumePendingCards();
    }

    async function downloadAgain(gallery, btn) {
        const card = btn.closest(".result-item[data-job-id]");
        if (!card) return;
        const jobId = card.dataset.jobId;
        btn.disabled = true;
        try {
            const r = await fetch(`${PREFIX}/${encodeURIComponent(jobId)}/replays`, {
                method: "POST",
            });
            if (!r.ok) throw new Error(`replay refused (${r.status})`);
            showFetching(gallery, jobId);
        } catch (err) {
            btn.disabled = false;
            console.error("download again failed", err);
        }
    }

    // The server's cards of these jobs, from the gallery's own pages (the
    // same partial the page renders).
    async function freshCards(gallery, jobIds) {
        const endpoint = gallery.dataset.galleryEndpoint;
        const wanted = new Set(jobIds);
        const found = [];
        let offset = 0;
        for (let page = 0; page < PAGES && offset !== null; page++) {
            const r = await fetch(`${endpoint}?offset=${offset}`);
            if (!r.ok) break;
            const box = document.createElement("div");
            box.innerHTML = await r.text();
            for (const card of box.querySelectorAll(".result-item[data-job-id]")) {
                if (wanted.has(card.dataset.jobId)) found.push(card);
            }
            if (jobIds.every((id) => found.some((c) => c.dataset.jobId === id))) break;
            const next = box.querySelector(".gallery-sentinel")?.dataset.nextOffset;
            offset = next === undefined ? null : Number(next);
        }
        return found;
    }

    // Links downloaded before: their cards, as the server draws them now,
    // at the first place. One being fetched again comes back running and is
    // polled.
    async function showFirst(gallery, jobIds) {
        if (!jobIds?.length) return;
        const fresh = await freshCards(gallery, jobIds);
        if (!fresh.length) return;
        for (const id of new Set(fresh.map((card) => card.dataset.jobId))) {
            cardsOf(gallery, id).forEach((card) => card.remove());
        }
        gallery.prepend(...fresh);
        window.Generation.resumePendingCards();
    }

    // A player whose video was removed while the page was open: the server
    // answers 410, and the card is drawn again as the server has it now (its
    // poster and the Download that fetches it), where it is.
    async function onMediaError(gallery, video) {
        const card = video.closest(".result-item[data-job-id]");
        if (!card || card.dataset.checkedGone) return;
        card.dataset.checkedGone = "1";
        const r = await fetch(video.currentSrc || video.src, {
            headers: { Range: "bytes=0-0" },
        });
        if (r.status !== 410) return;
        const jobId = card.dataset.jobId;
        const fresh = await freshCards(gallery, [jobId]);
        if (!fresh.length) return;
        const [first, ...rest] = cardsOf(gallery, jobId);
        rest.forEach((old) => old.remove());
        first.replaceWith(...fresh);
    }

    // "Download from Drive sheet": the button rests (disabled, counting
    // down in its label) after every click, and after a reload for what is
    // left of that rest (`data-cooldown-s`).
    const SHEET_LABEL = "Download from Drive sheet";

    function sheetLabel(btn, text) {
        const last = btn.lastChild;
        if (last?.nodeType === Node.TEXT_NODE) last.textContent = ` ${text}`;
        else btn.append(` ${text}`);
    }

    function restSheet(btn, seconds) {
        clearInterval(btn.restTimer);
        let left = Math.ceil(Number(seconds) || 0);
        const draw = () => {
            btn.disabled = left > 0;
            sheetLabel(btn, left > 0 ? `${SHEET_LABEL} (${left} s)` : SHEET_LABEL);
        };
        draw();
        if (left <= 0) return;
        btn.restTimer = setInterval(() => {
            left -= 1;
            draw();
            if (left <= 0) clearInterval(btn.restTimer);
        }, 1000);
    }

    function sheetStatus(text) {
        const box = document.querySelector("#results-panel .generation-status");
        if (box) box.textContent = text;
    }

    function sheetDay(iso) {
        const [, month, day] = iso.split("-");
        return `${day}/${month}`;
    }

    async function downloadSheet(btn) {
        btn.disabled = true;
        sheetStatus("Reading the Drive sheet…");
        let rest = 0;
        try {
            const r = await fetch(`${PREFIX}/sheet-downloads`, { method: "POST" });
            const answer = await r.json();
            if (!r.ok) {
                sheetStatus(typeof answer.detail === "string" ? answer.detail : "Could not read the Drive sheet");
                rest = Number(r.status === 429 ? /\d+/.exec(answer.detail)?.[0] : 0) || 0;
                return;
            }
            if (answer.job_id) {
                // The server draws the running card; generation.js polls it.
                window.location.reload();
                return;
            }
            rest = answer.cooldown_s;
            sheetStatus(`No new links for ${sheetDay(answer.day)}`);
        } catch (err) {
            sheetStatus("Could not read the Drive sheet");
            console.error("drive sheet download failed", err);
        } finally {
            restSheet(btn, rest);
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        const sheetButton = document.querySelector(".sheet-download");
        if (sheetButton) {
            restSheet(sheetButton, sheetButton.dataset.cooldownS);
            sheetButton.addEventListener("click", () => downloadSheet(sheetButton));
        }
        const panel = document.getElementById("results-panel");
        if (!panel) return;
        const gallery = panel.querySelector(".results");
        panel.addEventListener("click", (e) => {
            const btn = e.target.closest(".send-to-clone, .send-to-long");
            if (btn) send(btn);
            const again = e.target.closest(".download-again");
            if (again && gallery) downloadAgain(gallery, again);
        });
        if (!gallery) return;
        gallery.addEventListener("gallery:card-ready", (e) => decorate(e.target));
        // `error` does not bubble: caught on the way down.
        gallery.addEventListener(
            "error",
            (e) => {
                if (e.target instanceof HTMLVideoElement) {
                    onMediaError(gallery, e.target).catch((err) => {
                        console.error("could not redraw a removed video's card", err);
                    });
                }
            },
            true
        );
        document
            .getElementById("download-assets-form")
            ?.addEventListener("generation:queued", (e) => {
                showFirst(gallery, e.detail?.again).catch((err) => {
                    console.error("could not show the downloads asked for again", err);
                });
            });
    });
})();
