/*
 * The header's chats menu (components/ui.html `chats_menu`, features/chatting/alerts.py):
 * asks /chatting/alerts on load and again as often as the answer says. A failed ask is
 * tried again, so a deploy does not silence an open tab.
 * "Chats unanswered" is one line per model; every other one is one event's own. The
 * icon shows while there is any; the number on it counts the ones with something new.
 * Opening the list marks them seen in this browser until one of their `event_ids` is
 * new: for a model, a new fan left waiting. Remembered per event, so one fan answered
 * and another waiting at the same moment counts again though the number is the same.
 * Something new chimes once and notifies the desktop (notify.js), once per browser
 * however many tabs are open, and not again on the next page. Before the first
 * click allows sound, that waits for the click.
 * (Until 2026-11-18 each was a pop-up over the page, closed with x: "seen" keeps
 * the key those closes were remembered under.)
 */
(() => {
    const menu = document.querySelector(".chats-menu");
    const itemTemplate = document.getElementById("chats-item");
    if (!menu || !itemTemplate || !window.Notifier) return;
    const list = menu.querySelector(".chats-list");
    const count = menu.querySelector(".chats-count");
    const toggle = menu.querySelector("summary");
    const seenKey = "ofm_popups_closed";
    const chimedKey = "ofm_chats_chimed";
    const notifiedKey = "ofm_chats_notified";
    // A browser refusing storage still remembers for as long as this page is open.
    const memory = {};
    const askTimeoutMs = 30000;
    let storageRefused = false;
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
            // Reads may still work, and would bring back what this just marked.
            storageRefused = true;
        }
    }

    function isNew(popup, seen) {
        return popup.event_ids.some((id) => !seen.includes(id));
    }

    function fresh() {
        const seen = remembered(seenKey);
        return latest.filter((popup) => isNew(popup, seen));
    }

    function announce() {
        const now = fresh();
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

    function itemNode(popup, isFresh) {
        const node = itemTemplate.content.firstElementChild.cloneNode(true);
        node.href = popup.url;
        node.dataset.kind = popup.kind;
        node.toggleAttribute("data-new", isFresh);
        node.querySelector(".chats-item-who").textContent = `@${popup.username}`;
        node.querySelector(".chats-item-text").textContent = popup.text;
        return node;
    }

    /* The number on the icon; the list itself is left alone while it is open. */
    function showCount() {
        const n = fresh().length;
        count.textContent = n ? String(n) : "";
        count.hidden = n === 0;
        toggle.setAttribute("aria-label", n ? `Chats, ${n} new` : "Chats");
    }

    function showList() {
        const now = new Set(fresh().map((popup) => popup.id));
        list.replaceChildren(...latest.map((popup) => itemNode(popup, now.has(popup.id))));
    }

    /* Only what can still show needs remembering. */
    function forgetGone(found) {
        const current = found.flatMap((popup) => popup.event_ids);
        for (const key of [seenKey, chimedKey, notifiedKey]) {
            remember(
                key,
                remembered(key).filter((id) => current.includes(id)),
            );
        }
    }

    function show(found) {
        latest = found;
        forgetGone(found);
        menu.hidden = found.length === 0;
        if (!menu.open) showList();
        showCount();
        announce();
    }

    /* Opened: what is new stays marked in the list in front of the reader, and
       counts as seen from now on. */
    menu.addEventListener("toggle", () => {
        if (!menu.open) {
            showList();
            return;
        }
        remember(seenKey, [...new Set([...remembered(seenKey), ...latest.flatMap((popup) => popup.event_ids)])]);
        showCount();
    });

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
