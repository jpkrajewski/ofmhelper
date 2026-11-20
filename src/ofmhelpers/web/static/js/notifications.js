(() => {
    const menu = document.querySelector(".notifications-menu");
    const itemTemplate = document.getElementById("notifications-item");
    if (!menu || !itemTemplate || !window.Notifier) return;
    const list = menu.querySelector(".notifications-list");
    const count = menu.querySelector(".notifications-count");
    const toggle = menu.querySelector("summary");
    const seenKey = "ofm_popups_closed";
    const chimedKey = "ofm_chats_chimed";
    const notifiedKey = "ofm_chats_notified";
    const firstWaitMs = 3000;
    const longestWaitMs = 60000;
    const signedOut = 1008;

    const memory = {};
    let storageRefused = false;
    let waitMs = firstWaitMs;
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

            storageRefused = true;
        }
    }

    function isNew(item, seen) {
        return item.event_ids.some((id) => !seen.includes(id));
    }

    function fresh() {
        const seen = remembered(seenKey);
        return latest.filter((item) => isNew(item, seen));
    }

    function announce() {
        const now = fresh();
        const chimed = remembered(chimedKey);
        if (now.some((item) => isNew(item, chimed)) && window.Notifier.chime()) {
            remember(chimedKey, [...new Set([...chimed, ...now.flatMap((item) => item.event_ids)])]);
        }
        for (const item of now.filter((each) => isNew(each, remembered(notifiedKey)))) {
            if (window.Notifier.desktop(`${item.title}: ${item.text}`, item.id)) {
                remember(notifiedKey, [...new Set([...remembered(notifiedKey), ...item.event_ids])]);
            }
        }
    }

    function itemNode(item, isFresh) {
        const node = itemTemplate.content.firstElementChild.cloneNode(true);
        node.href = item.url;
        node.dataset.kind = item.kind;
        node.toggleAttribute("data-new", isFresh);
        node.querySelector(".notifications-item-title").textContent = item.title;
        node.querySelector(".notifications-item-text").textContent = item.text;
        return node;
    }

    function showCount() {
        const n = fresh().length;
        count.textContent = n ? String(n) : "";
        count.hidden = n === 0;
        toggle.setAttribute("aria-label", n ? `Notifications, ${n} new` : "Notifications");
    }

    function showList() {
        const now = new Set(fresh().map((item) => item.id));
        list.replaceChildren(...latest.map((item) => itemNode(item, now.has(item.id))));
    }

    function forgetGone(found) {
        const current = found.flatMap((item) => item.event_ids);
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

    menu.addEventListener("toggle", () => {
        if (!menu.open) {
            showList();
            return;
        }
        remember(seenKey, [...new Set([...remembered(seenKey), ...latest.flatMap((item) => item.event_ids)])]);
        showCount();
    });

    function connect() {
        const scheme = window.location.protocol === "https:" ? "wss" : "ws";
        const socket = new WebSocket(`${scheme}://${window.location.host}/notifications/ws`);
        socket.addEventListener("message", (event) => {
            waitMs = firstWaitMs;
            show(JSON.parse(event.data).items);
        });
        socket.addEventListener("close", (event) => {
            if (event.code === signedOut) return;
            setTimeout(connect, waitMs);
            waitMs = Math.min(waitMs * 2, longestWaitMs);
        });
    }

    window.Notifier.onUnlock(announce);
    connect();
})();
