/*
 * Every admin page's pop-ups for chats and sales (base.html,
 * features/chatting/alerts.py): asks /chatting/alerts on load and again as often as
 * the answer says. A failed ask is tried again, so a deploy does not silence an
 * open tab.
 * "Chats unanswered" is one pop-up per model; every other one is one event's own.
 * Closed (x), a pop-up stays away in this browser until one of its `event_ids` is
 * new: for a model, a new fan left waiting. Remembered per event, so one fan answered
 * and another waiting at the same moment brings it back though the number is the same.
 * Something new chimes once and notifies the desktop (notify.js), once per browser
 * however many tabs are open, and not again on the next page. Before the first
 * click allows sound, that waits for the click.
 */
(() => {
    const alerts = document.querySelector(".page-alerts");
    if (!alerts || !document.getElementById("chat-popup-info") || !window.Notifier) return;
    const closedKey = "ofm_popups_closed";
    const chimedKey = "ofm_chats_chimed";
    const notifiedKey = "ofm_chats_notified";
    // A browser refusing storage still remembers for as long as this page is open.
    const memory = {};
    const askTimeoutMs = 30000;
    let storageRefused = false;
    const popups = new Map();
    let checkEveryS = 60;
    let latest = [];

    function remembered(key) {
        if (storageRefused) return memory[key] || [];
        try {
            return JSON.parse(window.localStorage.getItem(key)) || [];
        } catch {
            return memory[key] || [];
        }
    }

    function remember(key, ids) {
        memory[key] = ids;
        try {
            window.localStorage.setItem(key, JSON.stringify(ids));
        } catch {
            // Reads may still work, and would bring back what this just closed.
            storageRefused = true;
        }
    }

    function isNew(popup, seen) {
        return popup.event_ids.some((id) => !seen.includes(id));
    }

    function link(popup) {
        const a = document.createElement("a");
        a.href = popup.url;
        a.textContent = `@${popup.username}`;
        return a;
    }

    function announce() {
        const now = latest.filter((popup) => popups.has(popup.id));
        const chimed = remembered(chimedKey);
        if (now.some((popup) => isNew(popup, chimed)) && window.Notifier.chime()) {
            remember(chimedKey, [...new Set([...chimed, ...now.flatMap((popup) => popup.event_ids)])]);
        }
        for (const popup of now.filter((each) => isNew(each, remembered(notifiedKey)))) {
            if (window.Notifier.desktop(`${popup.text}: @${popup.username}`, popup.id)) {
                remember(notifiedKey, [...new Set([...remembered(notifiedKey), ...popup.event_ids])]);
            }
        }
    }

    function closePopup(id) {
        const current = latest.find((popup) => popup.id === id);
        remember(closedKey, [...new Set([...remembered(closedKey), ...(current ? current.event_ids : [])])]);
        popups.get(id)?.remove();
        popups.delete(id);
    }

    function popupNode(popup) {
        const template = document.getElementById(`chat-popup-${popup.kind}`);
        const node = template.content.firstElementChild.cloneNode(true);
        node.querySelector(".chat-popup-link").replaceChildren(link(popup));
        node.querySelector(".chat-popup-dismiss").addEventListener("click", () => closePopup(popup.id));
        return node;
    }

    /* In the answer's order; one already up stays put, so nothing flickers at each ask. */
    function showPopups(found) {
        const closed = remembered(closedKey);
        const wanted = found.filter((popup) => isNew(popup, closed));
        const wantedIds = new Set(wanted.map((popup) => popup.id));
        for (const [id, node] of popups) {
            if (!wantedIds.has(id)) {
                node.remove();
                popups.delete(id);
            }
        }
        let next = alerts.querySelector(".chat-popup");
        for (const popup of wanted) {
            if (!popups.has(popup.id)) popups.set(popup.id, popupNode(popup));
            const node = popups.get(popup.id);
            // A model's number moves as fans wait and are answered.
            node.querySelector(".chat-popup-text").textContent = popup.text;
            if (node === next) next = next.nextElementSibling;
            else alerts.insertBefore(node, next);
        }
    }

    /* Only what can still show needs remembering. */
    function forgetGone(found) {
        const current = found.flatMap((popup) => popup.event_ids);
        for (const key of [closedKey, chimedKey, notifiedKey]) {
            remember(
                key,
                remembered(key).filter((id) => current.includes(id)),
            );
        }
    }

    function show(found) {
        latest = found;
        forgetGone(found);
        showPopups(found);
        announce();
    }

    function ask() {
        fetch("/chatting/alerts", { signal: AbortSignal.timeout(askTimeoutMs) })
            .then((r) => (r.ok ? r.json() : null))
            .then((found) => {
                if (!found) return;
                show(found.popups);
                checkEveryS = found.check_every_s;
            })
            .catch(() => {})
            .finally(() => setTimeout(ask, checkEveryS * 1000));
    }

    window.Notifier.onUnlock(announce);
    ask();
})();
