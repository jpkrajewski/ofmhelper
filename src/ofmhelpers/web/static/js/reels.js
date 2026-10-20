/*
 * /reels page controller.
 *
 * Add & scan: POST /reels/watch adds the profiles (to the typed group) and
 * queues their first scan on the worker. Pending rows carry data-pending;
 * the page polls GET /reels/watchlist until none is left, then reloads so the
 * feed (server-sorted, paged by gallery-scroll.js) shows the new reels. A
 * reload mid-scan picks the polling up again. ✕ stops watching a profile and
 * drops its cards; a row's group dropdown moves it (POST /reels/group/...),
 * the search box filters rows by name.
 *
 * Cards: only a lazy thumbnail is in the page; the first hover creates the
 * <video> (straight from Instagram's CDN) and plays it with sound -- browsers
 * allow sound only once the page has had a click or key press, so before
 * that it plays muted. A click or Enter/Space anywhere on a card picks it.
 * Picks live in sessionStorage (code -> mp4 URL), so they survive paging,
 * switching group and reloads; Clone selected posts them, the server saves
 * them into the asset store and answers the /clone URL that opens with them
 * in the videos picker.
 */
(() => {
    const PICKS_KEY = "reels-picks";
    const POLL_MS = 4000;

    function loadPicks() {
        try {
            return new Map(JSON.parse(sessionStorage.getItem(PICKS_KEY) || "[]"));
        } catch {
            return new Map();
        }
    }

    function savePicks(picks) {
        try {
            sessionStorage.setItem(PICKS_KEY, JSON.stringify([...picks]));
        } catch {
            // storage unavailable: picks just don't outlive the page
        }
    }

    function setStatus(statusEl, lines, isError) {
        statusEl.innerHTML = "";
        lines.forEach((line) => {
            const el = document.createElement("div");
            if (isError) el.className = "generation-error";
            el.textContent = line;
            statusEl.appendChild(el);
        });
    }

    async function errorDetail(response) {
        try {
            const data = await response.json();
            if (typeof data.detail === "string") return data.detail;
        } catch {
            // not JSON: fall through to the status line
        }
        return `Request failed (${response.status})`;
    }

    function formatTimes(root) {
        root.querySelectorAll("time[data-ts]:not([datetime])").forEach((el) => {
            const date = new Date(Number(el.dataset.ts) * 1000);
            el.dateTime = date.toISOString();
            el.textContent = date.toLocaleDateString([], { day: "numeric", month: "short" });
        });
    }

    function playWithSound(card) {
        let video = card.querySelector("video");
        if (!video) {
            video = document.createElement("video");
            video.className = "reel-video";
            video.src = card.dataset.videoUrl;
            video.loop = true;
            video.playsInline = true;
            card.querySelector(".reel-media").append(video);
        }
        video.muted = false;
        video.play().catch(() => {
            video.muted = true;
            video.play().catch(() => {});
        });
    }

    function initFeed(feed, picks, refresh) {
        const toggle = (card) => {
            if (card.getAttribute("aria-disabled") === "true") return; // login-only reel
            const on = !picks.has(card.dataset.code);
            if (on) picks.set(card.dataset.code, card.dataset.videoUrl);
            else picks.delete(card.dataset.code);
            savePicks(picks);
            refresh();
        };
        feed.addEventListener("click", (e) => {
            if (e.target.closest("a")) return;
            const card = e.target.closest(".reel-card");
            if (card) toggle(card);
        });
        feed.addEventListener("keydown", (e) => {
            const card = e.target.closest(".reel-card");
            if (!card || (e.key !== "Enter" && e.key !== " ")) return;
            e.preventDefault();
            toggle(card);
        });
        // mouseenter/leave don't bubble; capture them on the container.
        feed.addEventListener(
            "mouseenter",
            (e) => {
                const card = e.target;
                if (card.classList?.contains("reel-card") && card.dataset.videoUrl) playWithSound(card);
            },
            true
        );
        feed.addEventListener(
            "mouseleave",
            (e) => {
                if (e.target.classList?.contains("reel-card")) e.target.querySelector("video")?.pause();
            },
            true
        );
        feed.addEventListener("gallery-scroll:append", refresh);
    }

    function initSearch(watchList) {
        const search = document.getElementById("reels-watch-search");
        const apply = () => {
            const needle = search.value.trim().replace(/^@/, "").toLowerCase();
            watchList.querySelectorAll(".reels-watch-item").forEach((row) => {
                row.hidden = needle !== "" && !row.dataset.username.includes(needle);
            });
        };
        search.addEventListener("input", apply);
        return apply;
    }

    function initWatchList(feed, watchList, refresh) {
        const filteredGroup = new URL(watchList.dataset.endpoint, window.location.origin).searchParams.get("group");
        watchList.addEventListener("change", async (e) => {
            const select = e.target.closest(".reels-row-group");
            if (!select) return;
            const row = select.closest(".reels-watch-item");
            const username = row.dataset.username;
            const body = new FormData();
            body.set("group", select.value);
            const response = await fetch(`/reels/group/${encodeURIComponent(username)}`, { method: "POST", body });
            if (!response.ok) return;
            // On a group's page, a profile moved out of it leaves the page.
            if (filteredGroup && select.value !== filteredGroup) {
                row.remove();
                feed.querySelectorAll(".reel-card").forEach((card) => {
                    if (card.dataset.username === username) card.remove();
                });
                refresh();
            }
        });
        watchList.addEventListener("click", async (e) => {
            const row = e.target.closest(".reels-unwatch")?.closest(".reels-watch-item");
            if (!row) return;
            const username = row.dataset.username;
            const response = await fetch(`/reels/unwatch/${encodeURIComponent(username)}`, { method: "POST" });
            if (!response.ok) return;
            row.remove();
            feed.querySelectorAll(".reel-card").forEach((card) => {
                if (card.dataset.username === username) card.remove();
            });
            refresh();
        });
    }

    // Profiles just added are remembered until their first scans are done:
    // a deleted or private one is dropped by its scan, and once nothing is
    // pending, the ones missing from the list are the ones not added.
    const ADDED_KEY = "reels-added";

    function rememberAdded(usernames) {
        try {
            const earlier = JSON.parse(sessionStorage.getItem(ADDED_KEY) || "[]");
            sessionStorage.setItem(ADDED_KEY, JSON.stringify([...earlier, ...usernames]));
        } catch {
            // storage unavailable: just no "not added" report
        }
    }

    async function reportNotAdded(watchList, statusEl) {
        if (watchList.querySelector("[data-pending]")) return;
        let added = [];
        try {
            added = JSON.parse(sessionStorage.getItem(ADDED_KEY) || "[]");
            sessionStorage.removeItem(ADDED_KEY);
        } catch {
            return;
        }
        if (!added.length) return;
        // The whole list, not this page's: it may be filtered to one group.
        const response = await fetch("/reels/watchlist");
        if (!response.ok) return;
        const rows = document.createElement("template");
        rows.innerHTML = await response.text();
        const listed = new Set(
            [...rows.content.querySelectorAll(".reels-watch-item")].map((row) => row.dataset.username)
        );
        const notAdded = added.filter((username) => !listed.has(username));
        if (!notAdded.length) return;
        setStatus(
            statusEl,
            [`Not added — deleted, renamed or private: ${notAdded.map((u) => `@${u}`).join(", ")}`],
            true
        );
    }

    // Poll the watch list while any first scan runs; reload once none is left.
    function pollPending(watchList, statusEl, applySearch) {
        if (!watchList.querySelector("[data-pending]")) return;
        const tick = async () => {
            try {
                const response = await fetch(watchList.dataset.endpoint);
                if (response.ok) {
                    watchList.innerHTML = await response.text();
                    applySearch();
                }
            } catch {
                // keep polling: a blip must not strand the page
            }
            const pending = watchList.querySelectorAll("[data-pending]").length;
            if (!pending) {
                window.location.reload();
                return;
            }
            setStatus(statusEl, [`Scanning in the background… ${pending} profile(s) left.`], false);
            setTimeout(tick, POLL_MS);
        };
        setTimeout(tick, POLL_MS);
    }

    function initAdd(form, watchList, applySearch) {
        const statusEl = form.querySelector(".generation-status");
        const submitBtn = form.querySelector('button[type="submit"]');
        const groupSelect = form.elements.group;
        const newRow = document.getElementById("reels-group-new-row");
        const newGroup = document.getElementById("reels-group-new");

        // "Create" stores the group, then offers it in every dropdown on the
        // page -- the add form's (selected, for the profiles added next) and
        // each watch-list row's.
        document.getElementById("reels-group-add").addEventListener("click", () => {
            newRow.hidden = false;
            newGroup.focus();
        });
        const create = async () => {
            if (!newGroup.value.trim()) return;
            const body = new FormData();
            body.set("name", newGroup.value);
            const response = await fetch("/reels/groups", { method: "POST", body });
            if (!response.ok) {
                setStatus(statusEl, [await errorDetail(response)], true);
                return;
            }
            const { group } = await response.json();
            [groupSelect, ...document.querySelectorAll(".reels-row-group")].forEach((select) => {
                if (![...select.options].some((o) => o.value === group)) select.add(new Option(group, group));
            });
            groupSelect.value = group;
            newGroup.value = "";
            newRow.hidden = true;
            setStatus(statusEl, [`Group "${group}" created — pick it on any profile, or add profiles to it above.`], false);
        };
        document.getElementById("reels-group-create").addEventListener("click", create);
        newGroup.addEventListener("keydown", (e) => {
            if (e.key !== "Enter") return;
            e.preventDefault();
            create();
        });

        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            if (!form.elements.profiles.value.trim()) {
                setStatus(statusEl, ["Add at least one profile."], true);
                form.elements.profiles.focus();
                return;
            }
            const body = new FormData(form);
            submitBtn.disabled = true;
            try {
                const response = await fetch(form.action, { method: "POST", body });
                if (!response.ok) {
                    setStatus(statusEl, [await errorDetail(response)], true);
                    return;
                }
                const { added, already_watched: skipped } = await response.json();
                form.elements.profiles.value = "";
                rememberAdded(added);
                const lines = [`Added ${added.length} profile(s); scanning in the background.`];
                if (skipped) lines.push(`${skipped} already watched — moved to this group.`);
                setStatus(statusEl, lines, false);
                // A new group has to be in every dropdown: the reload after
                // the scans brings it; until then the rows refresh in place.
                const rows = await fetch(watchList.dataset.endpoint);
                if (rows.ok) watchList.innerHTML = await rows.text();
                applySearch();
                pollPending(watchList, statusEl, applySearch);
            } catch {
                setStatus(statusEl, ["Network error — try again."], true);
            } finally {
                submitBtn.disabled = false;
            }
        });
    }

    function initClone(form, picks, cloneBtn, refresh) {
        const statusEl = document.getElementById("reels-clone-status");
        const maxVideos = Number(form.dataset.maxVideos);

        cloneBtn.addEventListener("click", async () => {
            if (picks.size > maxVideos) {
                setStatus(statusEl, [`Pick at most ${maxVideos} reels.`], true);
                return;
            }
            const reels = [...picks].map(([code, video_url]) => ({ code, video_url }));
            const body = new FormData();
            body.append("reels", JSON.stringify(reels));
            cloneBtn.disabled = true;
            setStatus(statusEl, [`Downloading ${reels.length} reel${reels.length === 1 ? "" : "s"}…`], false);
            try {
                const response = await fetch(form.dataset.cloneAction, { method: "POST", body });
                if (!response.ok) {
                    setStatus(statusEl, [await errorDetail(response)], true);
                    refresh();
                    return;
                }
                picks.clear();
                savePicks(picks);
                window.location.href = (await response.json()).redirect;
            } catch {
                setStatus(statusEl, ["Network error — try again."], true);
                refresh();
            }
        });
    }

    // Download selected: the server fetches the picks and answers one mp4 or
    // a zip, saved through a blob link (the CDN URLs are cross-origin, so an
    // <a download> straight to them would just open the video). Picks stay,
    // so the same reels can still be cloned.
    function initDownload(form, picks, downloadBtn, refresh) {
        const statusEl = document.getElementById("reels-clone-status");
        const maxVideos = Number(form.dataset.maxVideos);

        downloadBtn.addEventListener("click", async () => {
            if (picks.size > maxVideos) {
                setStatus(statusEl, [`Pick at most ${maxVideos} reels.`], true);
                return;
            }
            const reels = [...picks].map(([code, video_url]) => ({ code, video_url }));
            const body = new FormData();
            body.append("reels", JSON.stringify(reels));
            downloadBtn.disabled = true;
            setStatus(statusEl, [`Downloading ${reels.length} reel${reels.length === 1 ? "" : "s"}…`], false);
            try {
                const response = await fetch(form.dataset.downloadAction, { method: "POST", body });
                if (!response.ok) {
                    setStatus(statusEl, [await errorDetail(response)], true);
                    return;
                }
                const name = /filename="?([^";]+)"?/.exec(response.headers.get("Content-Disposition") || "");
                const url = URL.createObjectURL(await response.blob());
                const link = document.createElement("a");
                link.href = url;
                link.download = name ? name[1] : "reels.zip";
                link.click();
                URL.revokeObjectURL(url);
                setStatus(statusEl, [`Downloaded ${reels.length} reel${reels.length === 1 ? "" : "s"}.`], false);
            } catch {
                setStatus(statusEl, ["Network error — try again."], true);
            } finally {
                refresh();
            }
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("reels-form");
        const feed = document.getElementById("reels-feed");
        const watchList = document.getElementById("reels-watch-list");
        const empty = document.getElementById("reels-empty");
        const cloneBtn = document.getElementById("reels-clone");
        const downloadBtn = document.getElementById("reels-download");
        const pickedCount = document.getElementById("reels-picked-count");
        const picks = loadPicks();
        const refresh = () => {
            formatTimes(feed);
            feed.querySelectorAll(".reel-card").forEach((card) => {
                card.setAttribute("aria-pressed", String(picks.has(card.dataset.code)));
            });
            pickedCount.textContent = picks.size
                ? `${picks.size} reel${picks.size === 1 ? "" : "s"} selected`
                : "No reels selected";
            cloneBtn.disabled = picks.size === 0;
            downloadBtn.disabled = picks.size === 0;
            document.getElementById("reels-watch-count").textContent = watchList.children.length;
            empty.hidden = feed.querySelector(".reel-card") !== null;
        };
        const applySearch = initSearch(watchList);
        initFeed(feed, picks, refresh);
        initWatchList(feed, watchList, refresh);
        initAdd(form, watchList, applySearch);
        initClone(form, picks, cloneBtn, refresh);
        initDownload(form, picks, downloadBtn, refresh);
        refresh();
        reportNotAdded(watchList, form.querySelector(".generation-status"));
        pollPending(watchList, form.querySelector(".generation-status"), applySearch);
    });
})();
