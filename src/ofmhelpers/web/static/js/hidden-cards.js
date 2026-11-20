(() => {

    const saving = new Map();

    let stash = null;

    let unhidden = false;

    const gallery = () => document.querySelector("#results-panel .results");
    const toggleButton = () => document.querySelector(".hidden-toggle");

    function hiddenCount(button) {
        return Number(button.dataset.hiddenCount) || 0;
    }

    function setLabel(button, text) {
        const node = [...button.childNodes].reverse().find((n) => n.nodeType === Node.TEXT_NODE);
        if (node) node.nodeValue = ` ${text}`;
        else button.append(` ${text}`);
    }

    function render(button, count) {
        const showing = stash !== null;
        button.dataset.hiddenCount = String(count);

        const row = button.closest(".ph-toolbar");
        const idle = count === 0 && !showing;
        const kept = "keep" in row.dataset;
        row.hidden = idle && !kept;
        button.hidden = idle && kept;
        button.setAttribute("aria-pressed", String(showing));
        setLabel(button, showing ? "Hide hidden" : `Show hidden (${count})`);
    }

    function endpoint(list, hidden) {
        const url = new URL(list.dataset.galleryEndpoint, window.location.origin);
        if (hidden) url.searchParams.set("hidden", "1");
        else url.searchParams.delete("hidden");
        return url.pathname + url.search;
    }

    function cardsOf(list, jobId) {
        return [...list.querySelectorAll(`.result-item[data-job-id="${CSS.escape(jobId)}"]`)];
    }

    function shiftSentinel(list, by) {
        const sentinel = list.querySelector(".gallery-sentinel");
        if (!sentinel) return;
        sentinel.dataset.nextOffset = String(Math.max(0, Number(sentinel.dataset.nextOffset) + by));
    }

    function fragmentOf(html) {
        const holder = document.createElement("template");
        holder.innerHTML = html;
        return holder.content;
    }

    async function fetchView(list, hidden) {
        const r = await fetch(endpoint(list, hidden));
        if (!r.ok) throw new Error(`failed (${r.status})`);
        return fragmentOf(await r.text());
    }

    function swapped(list) {
        window.Generation?.resumePendingCards();
        list.dispatchEvent(new CustomEvent("gallery:replaced"));
    }

    async function save(jobId, hidden) {
        const r = await fetch(`/jobs/${encodeURIComponent(jobId)}/hidden`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ hidden }),
        });
        if (r.ok) return;
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `failed (${r.status})`);
    }

    function setHidden(item, hidden) {
        const list = gallery();
        const button = toggleButton();
        const card = item.closest(".result-item[data-job-id]");
        if (!list || !button || !card) return;
        const jobId = card.dataset.jobId;
        const view = stash;
        const removed = cardsOf(list, jobId).map((c) => [c, c.nextSibling]);
        removed.forEach(([c]) => c.remove());
        shiftSentinel(list, -1);
        const before = hiddenCount(button);
        render(button, before + (hidden ? 1 : -1));
        if (!hidden) unhidden = true;

        const queued = (saving.get(jobId) || Promise.resolve())
            .catch(() => {})
            .then(() => save(jobId, hidden));
        saving.set(jobId, queued);
        queued
            .finally(() => {
                if (saving.get(jobId) === queued) saving.delete(jobId);
            })
            .catch((err) => {

                if (stash === view) {
                    removed.forEach(([c, next]) =>
                        list.insertBefore(c, next?.parentNode === list ? next : null)
                    );
                    shiftSentinel(list, 1);
                }
                render(button, hiddenCount(button) + (hidden ? -1 : 1));
                window.alert(`Could not ${hidden ? "hide" : "unhide"} it: ${err.message}`);
            });

        if (!hidden && hiddenCount(button) === 0) showNormal(list, button);
    }

    async function showHidden(list, button) {
        button.disabled = true;
        try {

            await Promise.allSettled([...saving.values()]);
            const cards = await fetchView(list, true);
            stash = document.createDocumentFragment();
            stash.append(...list.childNodes);
            list.replaceChildren(cards);
            list.dataset.galleryEndpoint = endpoint(list, true);
            list.setAttribute("data-live-paused", "");
            unhidden = false;
            render(button, hiddenCount(button));
            swapped(list);
        } catch (err) {
            window.alert(`Could not load the hidden cards: ${err.message}`);
        } finally {
            button.disabled = false;
        }
    }

    function showNormal(list, button) {
        if (stash === null) return;
        list.replaceChildren(stash);
        stash = null;
        list.dataset.galleryEndpoint = endpoint(list, false);
        list.removeAttribute("data-live-paused");
        render(button, hiddenCount(button));
        swapped(list);
        if (unhidden) refreshNormal(list);
    }

    async function refreshNormal(list) {
        unhidden = false;
        try {
            await Promise.allSettled([...saving.values()]);
            const cards = await fetchView(list, false);
            if (stash !== null) return;
            const fresh = [...cards.childNodes].map((node) => {
                const kept = node.dataset?.jobId && cardsOf(list, node.dataset.jobId);
                return kept?.length ? kept : [node];
            });

            const sending = [...list.children].filter(
                (el) => el.matches(".result-item") && !el.dataset.jobId
            );
            list.replaceChildren(...sending, ...fresh.flat());
            swapped(list);
        } catch (err) {
            window.alert(`Could not reload the cards: ${err.message}`);
        }
    }

    document.addEventListener("click", (e) => {
        const button = e.target.closest(".hidden-toggle");
        if (button) {
            const list = gallery();
            if (!list) return;
            if (stash === null) showHidden(list, button);
            else showNormal(list, button);
            return;
        }
        const item = e.target.closest(".hide-btn, .unhide-btn");
        if (item) setHidden(item, item.classList.contains("hide-btn"));
    });

    document.addEventListener(
        "submit",
        () => {
            const list = gallery();
            const button = toggleButton();
            if (list && button) showNormal(list, button);
        },
        true
    );
})();
