/*
 * Hidden gallery cards (/generate, /clone, /clone-long, /download-assets).
 *
 * Hide in a card's ⋯ menu takes it out of the gallery at once and saves in
 * the background (POST /jobs/{id}/hidden; a failed save puts it back and
 * says so). The server leaves hidden jobs out of the gallery's pages; the
 * job and its files stay.
 *
 * "Show hidden (N)" in the toolbar under the title (_gallery_hidden.html)
 * switches the gallery to only the hidden cards -- `?hidden=1` on its
 * data-gallery-endpoint, paged by gallery-scroll.js like the normal list --
 * each with Unhide, which takes it out of that view. live-gallery.js is
 * paused meanwhile. "Hide hidden" (or the last Unhide, or starting a run)
 * switches back: the normal cards were only set aside, so they come back
 * as they were -- refetched only when an Unhide put a card back among them.
 */
(() => {
    // Per job, the last save: the next waits for it, so a quick Hide then
    // Unhide reach the server in that order.
    const saving = new Map();
    // The normal view's nodes while the hidden one shows (null: normal).
    let stash = null;
    // An Unhide in the hidden view: the normal one needs its first page again.
    let unhidden = false;

    const gallery = () => document.querySelector("#results-panel .results");
    const toggleButton = () => document.querySelector(".hidden-toggle");

    function hiddenCount(button) {
        return Number(button.dataset.hiddenCount) || 0;
    }

    // The button's label is its text after the icon.
    function setLabel(button, text) {
        const node = [...button.childNodes].reverse().find((n) => n.nodeType === Node.TEXT_NODE);
        if (node) node.nodeValue = ` ${text}`;
        else button.append(` ${text}`);
    }

    // The toolbar row under the title is there only while one is hidden.
    function render(button, count) {
        const showing = stash !== null;
        button.dataset.hiddenCount = String(count);
        button.closest(".ph-toolbar").hidden = count === 0 && !showing;
        button.setAttribute("aria-pressed", String(showing));
        setLabel(button, showing ? "Hide hidden" : `Show hidden (${count})`);
    }

    // The gallery's endpoint for the hidden view or the normal one: what a
    // switch and every later page (gallery-scroll.js) fetch.
    function endpoint(list, hidden) {
        const url = new URL(list.dataset.galleryEndpoint, window.location.origin);
        if (hidden) url.searchParams.set("hidden", "1");
        else url.searchParams.delete("hidden");
        return url.pathname + url.search;
    }

    function cardsOf(list, jobId) {
        return [...list.querySelectorAll(`.result-item[data-job-id="${CSS.escape(jobId)}"]`)];
    }

    // The server's list for this view moved by `by` above the sentinel: the
    // next page starts that much later (gallery-scroll.js skips a card it
    // already has).
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

    // Hide (normal view) or Unhide (hidden view): either way the card leaves
    // the view it is in, at once.
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
                // Back where it was, if that view is still the one shown.
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
            // A Hide clicked just before must be saved first, or its card
            // would be missing from the hidden view.
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

    // After an Unhide: the first page again, so the card is back in its
    // place; a card already on the page is moved, not redrawn.
    async function refreshNormal(list) {
        unhidden = false;
        try {
            await Promise.allSettled([...saving.values()]);
            const cards = await fetchView(list, false);
            if (stash !== null) return; // switched to the hidden view meanwhile
            const fresh = [...cards.childNodes].map((node) => {
                const kept = node.dataset?.jobId && cardsOf(list, node.dataset.jobId);
                return kept?.length ? kept : [node];
            });
            // A run still being sent has no job yet: it stays on top.
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

    // Starting a run shows the normal view first: its card goes there.
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
