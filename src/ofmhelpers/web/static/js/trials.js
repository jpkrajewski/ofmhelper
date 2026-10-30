/*
 * /trials and /trials/{login}.
 *
 * /trials: the search box (#tr-search) filters the phone cards; a card's
 * Generate trials starts the job (fetch) and opens its folder.
 *
 * /trials/{login} runs without page reloads:
 *   a button (Apply caption, Regenerate, Upload, Generate trials) -> its
 *       card or button changes at once, the form is sent with fetch (the
 *       server answers JSON at once; the work is a job), then polling
 *       starts
 *   Select -> kept at once (POST .../select); "Upload N to Drive" counts it
 *   polling (#tr-grid[data-live], every 3 s while anything runs) -> the
 *       status line, and the cards whose data-stamp moved are fetched
 *       (GET data-cards?key=...) and swapped in one by one; a card being
 *       typed in or played waits for the next round
 */
(() => {
    const search = document.getElementById("tr-search");
    if (search) {
        search.addEventListener("input", () => {
            const words = search.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
            for (const card of document.querySelectorAll(".tr-card")) {
                const text = card.dataset.search || "";
                card.hidden = !words.every((w) => text.includes(w));
            }
        });
    }

    for (const form of document.querySelectorAll("form.tr-generate")) {
        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            form.querySelector("button").disabled = true;
            try {
                await fetch(form.action, { method: "POST" });
            } finally {
                location.href = form.action.replace(/\/generate$/, "");
            }
        });
    }

    const grid = document.getElementById("tr-grid");
    if (!grid) return;
    const status = document.getElementById("tr-status");
    const empty = document.getElementById("tr-empty");
    const upload = document.querySelector(".tr-upload-btn");
    const generate = document.querySelector("#tr-generate button");
    const drive = document.getElementById("tr-drive");
    const POLL_MS = 3000;
    let timer = null;

    const cardOf = (key) => grid.querySelector(`.tr-reel[data-key="${CSS.escape(key)}"]`);
    const picks = () => [...grid.querySelectorAll('input[name="approved"]')].filter((p) => p.checked && !p.disabled);

    function setLabel(button, text) {
        const node = [...button.childNodes].reverse().find((n) => n.nodeType === Node.TEXT_NODE);
        if (node) node.nodeValue = ` ${text}`;
    }

    function countPicks() {
        const n = picks().length;
        upload.disabled = n === 0;
        setLabel(upload, n ? `Upload ${n} to Drive` : "Upload to Drive");
    }

    // The card as it will be once the job runs. Its stamp is dropped, so
    // the next poll swaps in the server's card whatever happened.
    function showBusy(card, text) {
        card.dataset.stamp = "";
        for (const b of card.querySelectorAll("button, input[type=checkbox]")) b.disabled = true;
        const video = card.querySelector(".tr-video");
        if (!video) return;
        const box = document.createElement("div");
        box.className = "result-file tr-pending";
        box.innerHTML = `<span class="spinner"></span> ${text}`;
        video.replaceWith(box);
    }

    async function send(form, body) {
        const r = await fetch(form.action, { method: "POST", body });
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
    }

    // Every form on the page goes by fetch; what it changes shows at once.
    document.addEventListener("submit", async (e) => {
        const form = e.target;
        e.preventDefault();
        // Read first: a control disabled below would not be sent.
        const body = new FormData(form);
        const card = form.closest(".tr-reel");
        if (card) {
            if (card.dataset.busy === "true") return;
            card.dataset.busy = "true";
            showBusy(card, "generating…");
        } else if (form.id === "tr-upload") {
            const chosen = picks();
            if (!chosen.length) return;
            for (const p of chosen) {
                const c = p.closest(".tr-reel");
                c.dataset.busy = "true";
                showBusy(c, "uploading…");
            }
            countPicks();
        } else if (form.id === "tr-generate") {
            generate.disabled = true;
            status.textContent = "Making trials…";
        }
        try {
            await send(form, body);
        } catch {
            status.textContent = "Not sent: check your connection and try again.";
        }
        poll(true);
    });

    grid.addEventListener("change", (e) => {
        const box = e.target.closest('input[name="approved"]');
        if (!box) return;
        countPicks();
        const body = new FormData();
        body.set("selected", box.checked ? "true" : "false");
        fetch(`${grid.dataset.live.replace(/\/live$/, "")}/reel/${encodeURIComponent(box.value)}/select`, {
            method: "POST",
            body,
        }).catch(() => {});
    });

    // A card the user is in (typing a caption, a video playing) is left
    // alone this round.
    function inUse(card) {
        if (card.contains(document.activeElement) && document.activeElement.matches("textarea")) return true;
        const video = card.querySelector("video");
        return Boolean(video && !video.paused);
    }

    async function swap(keys) {
        const url = new URL(grid.dataset.cards, location.origin);
        for (const key of keys) url.searchParams.append("key", key);
        const r = await fetch(url);
        if (!r.ok) return;
        const holder = document.createElement("template");
        holder.innerHTML = await r.text();
        for (const fresh of holder.content.querySelectorAll(".tr-reel")) {
            const old = cardOf(fresh.dataset.key);
            if (old) old.replaceWith(fresh);
        }
        return holder;
    }

    async function apply(live) {
        status.textContent = live.status;
        generate.disabled = live.making || live.todo === 0;
        if (live.trials_url) {
            drive.href = live.trials_url;
            drive.hidden = false;
        }
        const wanted = live.cards.map((c) => c.key);
        // Gone (a pending card now made, a reel dropped): out.
        for (const card of grid.querySelectorAll(".tr-reel")) {
            if (!wanted.includes(card.dataset.key)) card.remove();
        }
        const missing = wanted.filter((k) => !cardOf(k));
        const changed = live.cards
            .filter((c) => {
                const card = cardOf(c.key);
                return card && card.dataset.stamp !== c.stamp && !inUse(card);
            })
            .map((c) => c.key);
        if (missing.length || changed.length) {
            const holder = await swap([...missing, ...changed]);
            // New cards go in page order.
            for (const key of missing) {
                const fresh = holder?.content.querySelector(`.tr-reel[data-key="${CSS.escape(key)}"]`);
                if (!fresh) continue;
                const next = wanted.slice(wanted.indexOf(key) + 1).map(cardOf).find(Boolean);
                grid.insertBefore(fresh, next || null);
            }
        }
        empty.hidden = grid.children.length > 0;
        countPicks();
    }

    async function poll(now) {
        clearTimeout(timer);
        if (!now) await new Promise((r) => setTimeout(r, POLL_MS));
        try {
            const r = await fetch(grid.dataset.live);
            if (!r.ok) throw new Error(String(r.status));
            const live = await r.json();
            await apply(live);
            if (live.running) timer = setTimeout(() => poll(true), POLL_MS);
        } catch {
            timer = setTimeout(() => poll(true), POLL_MS * 2);
        }
    }

    countPicks();
    if ("running" in grid.dataset) poll(false);
})();
